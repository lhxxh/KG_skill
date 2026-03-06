#!/usr/bin/env python3
"""
load_graph.py — Deterministic Neo4j loader (NO LLM)

Reads extraction JSON files and loads entities/relationships into Neo4j
using cypher-shell subprocess calls. Parses the schema file to create
constraints/indexes, and performs entity resolution via a 5-step cascade
(exact, case-insensitive, alias, fuzzy, create new).

Schema-driven: works with any schema, not hardcoded to a specific domain.

Usage:
    python3 scripts/load_graph.py --schema schema/pk_schema.md --init-schema
    python3 scripts/load_graph.py --schema schema/pk_schema.md output/paper_extraction.json
    python3 scripts/load_graph.py --schema schema/pk_schema.md output/*.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

# --- Configuration ---

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASSWORD", "docling-graph")

# Entity resolution fuzzy match threshold
FUZZY_THRESHOLD = 0.85

# --- Cypher Execution ---


def run_cypher(query, params=None):
    """Execute a Cypher query via cypher-shell and return stdout."""
    cmd = [
        "cypher-shell",
        "-u", NEO4J_USER,
        "-p", NEO4J_PASS,
        "-a", NEO4J_URI,
        "--format", "plain",
    ]

    if params:
        for key, value in params.items():
            if isinstance(value, str):
                cmd.extend(["--param", f'{key} => "{_escape(value)}"'])
            elif isinstance(value, list):
                items = ", ".join(
                    f'"{_escape(v)}"' if isinstance(v, str) else str(v)
                    for v in value
                )
                cmd.extend(["--param", f"{key} => [{items}]"])
            elif isinstance(value, (int, float)):
                cmd.extend(["--param", f"{key} => {value}"])
            elif value is None:
                cmd.extend(["--param", f"{key} => null"])

    result = subprocess.run(
        cmd,
        input=query,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower() or "equivalent" in stderr.lower():
            return result.stdout
        print(f"  [ERROR] Cypher failed: {stderr}", file=sys.stderr)
        print(f"  [QUERY] {query[:200]}", file=sys.stderr)
        return None

    return result.stdout


def _escape(s):
    """Escape string for Cypher string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


# --- Schema Parsing ---


def parse_schema(schema_path):
    """
    Parse a schema markdown file to extract labels, constraints, and fulltext indexes.

    Returns:
        {
            "labels": ["Drug", "Model", ...],
            "constraints": ["CREATE CONSTRAINT ... ;", ...],
            "fulltext_indexes": ["CREATE FULLTEXT INDEX ... ;", ...],
        }
    """
    text = Path(schema_path).read_text()
    result = {"labels": [], "constraints": [], "fulltext_indexes": []}

    # Extract labels from **Label** (description) lines
    for m in re.finditer(r'^\*\*(\w+)\*\*', text, re.MULTILINE):
        label = m.group(1)
        if label not in result["labels"]:
            result["labels"].append(label)

    # Parse constraints: `Label.property` — UNIQUE
    for m in re.finditer(r'`(\w+)\.(\w+)`\s*—\s*UNIQUE', text):
        label, prop = m.group(1), m.group(2)
        name = f"{label.lower()}_{prop}"
        stmt = (
            f"CREATE CONSTRAINT {name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE;"
        )
        result["constraints"].append(stmt)

    # Parse fulltext indexes: `index_name` on Label: `[prop1, prop2]`
    for m in re.finditer(r'`(\w+)`\s+on\s+(\w+):\s*`\[([^\]]+)\]`', text):
        idx_name, label, props_str = m.group(1), m.group(2), m.group(3)
        props = [p.strip() for p in props_str.split(",")]
        prop_list = ", ".join(f"n.{p}" for p in props)
        stmt = (
            f"CREATE FULLTEXT INDEX {idx_name} IF NOT EXISTS "
            f"FOR (n:{label}) ON EACH [{prop_list}];"
        )
        result["fulltext_indexes"].append(stmt)

    return result


# --- Schema Initialization ---


def init_schema(schema_info):
    """Create constraints and fulltext indexes from parsed schema (idempotent)."""
    print("=== Initializing Schema ===")

    for stmt in schema_info["constraints"]:
        run_cypher(stmt)
    print(f"  Created/verified {len(schema_info['constraints'])} constraints")

    for stmt in schema_info["fulltext_indexes"]:
        run_cypher(stmt)
    print(f"  Created/verified {len(schema_info['fulltext_indexes'])} fulltext indexes")

    # Verify
    out = run_cypher("SHOW CONSTRAINTS;")
    if out:
        count = len([l for l in out.strip().split("\n") if l.strip()]) - 1
        print(f"  Verified: {max(count, 0)} constraints in DB")

    out = run_cypher("SHOW INDEXES;")
    if out:
        count = len([l for l in out.strip().split("\n") if l.strip()]) - 1
        print(f"  Verified: {max(count, 0)} indexes in DB")
    print()


# --- Entity Resolution ---


def load_existing_nodes(labels):
    """Load all existing nodes from Neo4j into an in-memory registry."""
    registry = {}
    for label in labels:
        query = f"""
MATCH (n:{label})
RETURN n.canonical_name AS name, coalesce(n.aliases, []) AS aliases
"""
        out = run_cypher(query)
        nodes = []
        if out:
            for line in out.strip().split("\n")[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(", [")
                if len(parts) == 2:
                    name = parts[0].strip().strip('"')
                    alias_str = parts[1].rstrip("]").strip()
                    aliases = [a.strip().strip('"') for a in alias_str.split(",") if a.strip().strip('"')]
                elif line.strip():
                    name = line.split(",")[0].strip().strip('"')
                    aliases = []
                else:
                    continue
                if name:
                    nodes.append({"canonical_name": name, "aliases": aliases})
        registry[label] = nodes
    return registry


def resolve_entity(entity, registry):
    """
    5-step entity resolution cascade.
    Returns (resolved_name, action) where action is one of:
    'exact_match', 'case_match', 'alias_match', 'fuzzy_match', 'create_new'
    """
    label = entity["label"]
    name = entity["canonical_name"]
    existing = registry.get(label, [])

    # Step 1: Exact match
    for node in existing:
        if node["canonical_name"] == name:
            return name, "exact_match"

    # Step 2: Case-insensitive match
    for node in existing:
        if node["canonical_name"].lower() == name.lower():
            return node["canonical_name"], "case_match"

    # Step 3: Alias lookup
    for node in existing:
        if name.lower() in [a.lower() for a in node["aliases"]]:
            return node["canonical_name"], "alias_match"

    # Step 4: Fuzzy match
    best_score = 0
    best_name = None
    for node in existing:
        score = SequenceMatcher(None, name.lower(), node["canonical_name"].lower()).ratio()
        if score > best_score:
            best_score = score
            best_name = node["canonical_name"]

    if best_score >= FUZZY_THRESHOLD:
        return best_name, "fuzzy_match"

    # Step 5: Create new
    return name, "create_new"


# --- Generic Node MERGE ---


def _cypher_value(val):
    """Convert a Python value to a Cypher literal string."""
    if isinstance(val, str):
        return f'"{_escape(val)}"'
    elif isinstance(val, list):
        items = ", ".join(_cypher_value(v) for v in val)
        return f"[{items}]"
    elif isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif val is None:
        return "null"
    else:
        # Fallback: serialize as JSON string
        return f'"{_escape(json.dumps(val))}"'


def merge_node(entity, doi, json_filename):
    """MERGE a single node into Neo4j. Schema-agnostic: reads label and properties from the entity."""
    label = entity["label"]
    name = entity["canonical_name"]
    props = entity.get("properties", {})

    # Build ON CREATE SET clauses from all properties
    create_sets = []
    for key, val in props.items():
        # If the value is a dict, serialize as JSON string (Neo4j doesn't support map properties)
        if isinstance(val, dict):
            val = json.dumps(val)
        create_sets.append(f"  n.{key} = {_cypher_value(val)}")
    create_sets.append(f'  n.source_papers = ["{_escape(doi)}"]')
    create_sets.append(f"  n.created_at = datetime()")

    create_clause = ",\n".join(create_sets)

    query = f"""
MERGE (n:{label} {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
{create_clause}
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    result = run_cypher(query)
    return result is not None


# --- Relationship MERGE ---


def merge_relationship(rel, entities_by_id, doi, json_filename):
    """MERGE a relationship with full provenance."""
    source_entity = entities_by_id.get(rel["source_entity_id"])
    target_entity = entities_by_id.get(rel["target_entity_id"])

    if not source_entity or not target_entity:
        print(f"  [WARN] Relationship references unknown entity: {rel}", file=sys.stderr)
        return False

    source_label = source_entity["label"]
    source_name = source_entity["canonical_name"]
    target_label = target_entity["label"]
    target_name = target_entity["canonical_name"]
    rel_type = rel["type"]

    query = f"""
MATCH (source:{source_label} {{canonical_name: "{_escape(source_name)}"}})
MATCH (target:{target_label} {{canonical_name: "{_escape(target_name)}"}})
MERGE (source)-[r:{rel_type}]->(target)
ON CREATE SET
  r.source_papers = ["{_escape(doi)}"],
  r.extraction_source = "{_escape(json_filename)}",
  r.created_at = datetime()
ON MATCH SET
  r.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN r.source_papers
    THEN r.source_papers + "{_escape(doi)}"
    ELSE r.source_papers
  END
"""
    result = run_cypher(query)
    return result is not None


# --- Dedup Candidate Flagging ---


def flag_dedup_candidate(name, label, matched_name, score):
    """Flag a node as a dedup candidate."""
    reason = f"fuzzy_match(score={score:.2f}, matched={matched_name})"
    query = f"""
MATCH (n:{label} {{canonical_name: "{_escape(name)}"}})
SET n.dedup_candidate = true,
    n.dedup_reason = "{_escape(reason)}"
"""
    run_cypher(query)


# --- Main Loading Logic ---


def load_json(json_path, labels):
    """Load a single extraction JSON into Neo4j."""
    json_path = Path(json_path)
    json_filename = json_path.name
    print(f"=== Loading: {json_filename} ===")

    with open(json_path) as f:
        data = json.load(f)

    source_paper = data["source_paper"]
    doi = source_paper["doi"]
    entities = data["entities"]
    relationships = data["relationships"]

    print(f"  Paper: {source_paper['title'][:80]}...")
    print(f"  DOI: {doi}")
    print(f"  Entities: {len(entities)}, Relationships: {len(relationships)}")

    # Build entity lookup by id
    entities_by_id = {e["entity_id"]: e for e in entities}

    # Load existing nodes for entity resolution
    registry = load_existing_nodes(labels)

    # Stats
    stats = {
        "matched": 0,
        "created": 0,
        "fuzzy_flagged": 0,
        "merge_failed": 0,
        "rels_created": 0,
        "rels_failed": 0,
    }

    # --- Phase 1: Resolve and MERGE nodes ---
    print("\n  --- Entity Resolution & Node MERGE ---")
    for entity in entities:
        resolved_name, action = resolve_entity(entity, registry)

        if action == "create_new":
            stats["created"] += 1
            print(f"  [NEW]   {entity['label']:10} {entity['canonical_name']}")
        elif action == "fuzzy_match":
            stats["fuzzy_flagged"] += 1
            stats["matched"] += 1
            print(f"  [FUZZY] {entity['label']:10} {entity['canonical_name']} -> {resolved_name}")
        else:
            stats["matched"] += 1
            print(f"  [MATCH] {entity['label']:10} {entity['canonical_name']} ({action})")

        # Update entity canonical_name to resolved name
        entity["canonical_name"] = resolved_name

        # MERGE node
        success = merge_node(entity, doi, json_filename)
        if not success:
            stats["merge_failed"] += 1

        # Flag fuzzy matches as dedup candidates
        if action == "fuzzy_match":
            flag_dedup_candidate(
                resolved_name, entity["label"], entity["canonical_name"],
                SequenceMatcher(None, entity["canonical_name"].lower(), resolved_name.lower()).ratio()
            )

        # Update registry for subsequent resolution within same file
        label = entity["label"]
        existing_names = [n["canonical_name"] for n in registry.get(label, [])]
        if resolved_name not in existing_names:
            registry.setdefault(label, []).append({
                "canonical_name": resolved_name,
                "aliases": entity.get("properties", {}).get("aliases", []),
            })

    # --- Phase 2: MERGE relationships ---
    print("\n  --- Relationship MERGE ---")
    for rel in relationships:
        source = entities_by_id.get(rel["source_entity_id"], {})
        target = entities_by_id.get(rel["target_entity_id"], {})
        print(f"  ({source.get('canonical_name', '?')})-[:{rel['type']}]->({target.get('canonical_name', '?')})")

        success = merge_relationship(rel, entities_by_id, doi, json_filename)
        if success:
            stats["rels_created"] += 1
        else:
            stats["rels_failed"] += 1

    # --- Phase 3: Verification ---
    print("\n  --- Verification ---")
    out = run_cypher(f"""
MATCH (n) WHERE "{_escape(doi)}" IN n.source_papers
RETURN labels(n)[0] AS label, count(n) AS count
""")
    if out:
        print(f"  Nodes for this paper:")
        for line in out.strip().split("\n")[1:]:
            if line.strip():
                print(f"    {line.strip()}")

    out = run_cypher(f"""
MATCH ()-[r]->() WHERE "{_escape(doi)}" IN r.source_papers
RETURN type(r) AS rel_type, count(r) AS count
""")
    if out:
        print(f"  Relationships for this paper:")
        for line in out.strip().split("\n")[1:]:
            if line.strip():
                print(f"    {line.strip()}")

    # --- Summary ---
    print(f"\n  --- Summary ---")
    print(f"  Matched:  {stats['matched']}")
    print(f"  Created:  {stats['created']}")
    print(f"  Fuzzy:    {stats['fuzzy_flagged']}")
    print(f"  Failed:   {stats['merge_failed']}")
    print(f"  Rels OK:  {stats['rels_created']}")
    print(f"  Rels Err: {stats['rels_failed']}")
    print()

    return stats


def print_global_summary():
    """Print overall graph summary."""
    print("=== Global Graph Summary ===")
    out = run_cypher("""
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS total
ORDER BY total DESC
""")
    if out:
        for line in out.strip().split("\n"):
            if line.strip():
                print(f"  {line.strip()}")

    out = run_cypher("""
MATCH ()-[r]->()
RETURN type(r) AS rel_type, count(r) AS total
ORDER BY total DESC
""")
    if out:
        for line in out.strip().split("\n"):
            if line.strip():
                print(f"  {line.strip()}")

    # Edge provenance check
    print("\n  Edge provenance sample:")
    out = run_cypher("""
MATCH ()-[r]->()
RETURN type(r) AS rel, r.source_papers AS papers, r.extraction_source AS source
LIMIT 5
""")
    if out:
        for line in out.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")
    print()


# --- CLI ---


def main():
    parser = argparse.ArgumentParser(
        description="Load extraction JSONs into Neo4j (no LLM required)"
    )
    parser.add_argument(
        "json_files",
        nargs="*",
        help="Path(s) to extraction JSON files",
    )
    parser.add_argument(
        "--schema",
        default="schema/pk_schema.md",
        help="Path to schema markdown file (default: schema/pk_schema.md)",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Create constraints and fulltext indexes",
    )
    args = parser.parse_args()

    if not args.init_schema and not args.json_files:
        parser.print_help()
        sys.exit(1)

    # Parse schema
    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {args.schema}", file=sys.stderr)
        sys.exit(1)

    schema_info = parse_schema(schema_path)
    labels = schema_info["labels"]

    if not labels:
        print(f"[ERROR] No labels found in schema: {args.schema}", file=sys.stderr)
        sys.exit(1)

    print(f"Schema: {args.schema} ({len(labels)} labels: {', '.join(labels)})")

    if args.init_schema:
        init_schema(schema_info)

    if not args.json_files:
        return

    # Load each JSON
    all_stats = []
    failed = []
    for json_path in args.json_files:
        path = Path(json_path)
        if not path.exists():
            print(f"[ERROR] File not found: {json_path}", file=sys.stderr)
            failed.append(json_path)
            continue
        if not path.suffix == ".json":
            print(f"[WARN] Skipping non-JSON file: {json_path}", file=sys.stderr)
            continue
        try:
            stats = load_json(json_path, labels)
            all_stats.append((json_path, stats))
        except Exception as e:
            print(f"[ERROR] Failed to load {json_path}: {e}", file=sys.stderr)
            failed.append(json_path)

    # Global summary
    if all_stats:
        print_global_summary()

    # Final report
    print("=== Final Report ===")
    total_created = sum(s["created"] for _, s in all_stats)
    total_matched = sum(s["matched"] for _, s in all_stats)
    total_fuzzy = sum(s["fuzzy_flagged"] for _, s in all_stats)
    total_rels = sum(s["rels_created"] for _, s in all_stats)
    print(f"  Files loaded:  {len(all_stats)}")
    print(f"  Files failed:  {len(failed)}")
    print(f"  Nodes created: {total_created}")
    print(f"  Nodes matched: {total_matched}")
    print(f"  Fuzzy flags:   {total_fuzzy}")
    print(f"  Relationships: {total_rels}")

    if failed:
        print(f"\n  Failed files:")
        for f in failed:
            print(f"    - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
