#!/usr/bin/env python3
"""
load_graph.py — Deterministic Neo4j loader (NO LLM)

Reads extraction JSON files and loads entities/relationships into Neo4j
using cypher-shell subprocess calls. Performs entity resolution via a
5-step cascade (exact, case-insensitive, alias, fuzzy, create new).

Usage:
    python3 scripts/load_graph.py --init-schema
    python3 scripts/load_graph.py output/paper_extraction.json
    python3 scripts/load_graph.py output/*.json
    python3 scripts/load_graph.py --init-schema output/*.json
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
    # Build parameter string for cypher-shell
    cmd = [
        "cypher-shell",
        "-u", NEO4J_USER,
        "-p", NEO4J_PASS,
        "-a", NEO4J_URI,
        "--format", "plain",
    ]

    # cypher-shell accepts parameters via --param key=value
    if params:
        for key, value in params.items():
            if isinstance(value, str):
                cmd.extend(["--param", f'{key} => "{_escape(value)}"'])
            elif isinstance(value, list):
                # Format list as Cypher literal
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
        # Ignore "already exists" errors for constraints/indexes
        if "already exists" in stderr.lower() or "equivalent" in stderr.lower():
            return result.stdout
        print(f"  [ERROR] Cypher failed: {stderr}", file=sys.stderr)
        print(f"  [QUERY] {query[:200]}", file=sys.stderr)
        return None

    return result.stdout


def _escape(s):
    """Escape string for Cypher string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


# --- Schema Initialization ---

CONSTRAINTS = [
    'CREATE CONSTRAINT drug_canonical IF NOT EXISTS FOR (n:Drug) REQUIRE n.canonical_name IS UNIQUE;',
    'CREATE CONSTRAINT model_canonical IF NOT EXISTS FOR (n:Model) REQUIRE n.canonical_name IS UNIQUE;',
    'CREATE CONSTRAINT type_canonical IF NOT EXISTS FOR (n:Type) REQUIRE n.canonical_name IS UNIQUE;',
    'CREATE CONSTRAINT organism_canonical IF NOT EXISTS FOR (n:Organism) REQUIRE n.canonical_name IS UNIQUE;',
    'CREATE CONSTRAINT disease_canonical IF NOT EXISTS FOR (n:Disease) REQUIRE n.canonical_name IS UNIQUE;',
]

FULLTEXT_INDEXES = [
    'CREATE FULLTEXT INDEX drug_fulltext IF NOT EXISTS FOR (n:Drug) ON EACH [n.canonical_name, n.drug_name];',
    'CREATE FULLTEXT INDEX model_fulltext IF NOT EXISTS FOR (n:Model) ON EACH [n.canonical_name];',
    'CREATE FULLTEXT INDEX type_fulltext IF NOT EXISTS FOR (n:Type) ON EACH [n.canonical_name, n.model_type];',
    'CREATE FULLTEXT INDEX organism_fulltext IF NOT EXISTS FOR (n:Organism) ON EACH [n.canonical_name, n.organism];',
    'CREATE FULLTEXT INDEX disease_fulltext IF NOT EXISTS FOR (n:Disease) ON EACH [n.canonical_name, n.name];',
]


def init_schema():
    """Create constraints and fulltext indexes (idempotent)."""
    print("=== Initializing Schema ===")
    for stmt in CONSTRAINTS:
        run_cypher(stmt)
    print(f"  Created/verified {len(CONSTRAINTS)} constraints")

    for stmt in FULLTEXT_INDEXES:
        run_cypher(stmt)
    print(f"  Created/verified {len(FULLTEXT_INDEXES)} fulltext indexes")

    # Verify
    out = run_cypher("SHOW CONSTRAINTS;")
    if out:
        count = len([l for l in out.strip().split("\n") if l.strip()]) - 1  # minus header
        print(f"  Verified: {max(count, 0)} constraints in DB")

    out = run_cypher("SHOW INDEXES;")
    if out:
        count = len([l for l in out.strip().split("\n") if l.strip()]) - 1
        print(f"  Verified: {max(count, 0)} indexes in DB")
    print()


# --- Entity Resolution ---


def load_existing_nodes():
    """Load all existing nodes from Neo4j into an in-memory registry."""
    registry = {}  # {label: [{canonical_name, aliases}, ...]}
    for label in ["Drug", "Model", "Type", "Organism", "Disease"]:
        query = f"""
MATCH (n:{label})
RETURN n.canonical_name AS name, coalesce(n.aliases, []) AS aliases
"""
        out = run_cypher(query)
        nodes = []
        if out:
            for line in out.strip().split("\n")[1:]:  # skip header
                line = line.strip()
                if not line:
                    continue
                # Parse plain format: name, aliases
                parts = line.split(", [")
                if len(parts) == 2:
                    name = parts[0].strip().strip('"')
                    alias_str = parts[1].rstrip("]").strip()
                    aliases = [a.strip().strip('"') for a in alias_str.split(",") if a.strip().strip('"')]
                elif line.strip():
                    # Single column or no aliases
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


# --- Node MERGE ---


def merge_node(entity, doi, json_filename, is_new):
    """MERGE a single node into Neo4j following write-patterns.md."""
    label = entity["label"]
    name = entity["canonical_name"]
    props = entity.get("properties", {})

    if label == "Drug":
        aliases_val = props.get("aliases", [])
        aliases_cypher = ", ".join(f'"{_escape(a)}"' for a in aliases_val)
        query = f"""
MERGE (n:Drug {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
  n.drug_name = "{_escape(props.get('drug_name', name))}",
  n.drug_type = "{_escape(props.get('drug_type', ''))}",
  n.aliases = [{aliases_cypher}],
  n.source_papers = ["{_escape(doi)}"],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    elif label == "Model":
        equations = props.get("mathematical_equations", [])
        eq_cypher = ", ".join(f'"{_escape(e)}"' for e in equations)
        param_means = props.get("parameter_means", "{}")
        param_iiv = props.get("parameter_iiv_std_dev", "{}")
        # Ensure these are JSON strings, not dicts
        if isinstance(param_means, dict):
            param_means = json.dumps(param_means)
        if isinstance(param_iiv, dict):
            param_iiv = json.dumps(param_iiv)
        query = f"""
MERGE (n:Model {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
  n.mathematical_equations = [{eq_cypher}],
  n.parameter_means = '{_escape(param_means)}',
  n.parameter_iiv_std_dev = '{_escape(param_iiv)}',
  n.source_papers = ["{_escape(doi)}"],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    elif label == "Type":
        query = f"""
MERGE (n:Type {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
  n.model_type = "{_escape(props.get('model_type', name))}",
  n.source_papers = ["{_escape(doi)}"],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    elif label == "Organism":
        query = f"""
MERGE (n:Organism {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
  n.organism = "{_escape(props.get('organism', name))}",
  n.source_papers = ["{_escape(doi)}"],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    elif label == "Disease":
        aliases_val = props.get("aliases", [])
        aliases_cypher = ", ".join(f'"{_escape(a)}"' for a in aliases_val)
        query = f"""
MERGE (n:Disease {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
  n.name = "{_escape(props.get('name', name))}",
  n.aliases = [{aliases_cypher}],
  n.source_papers = ["{_escape(doi)}"],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(doi)}" IN n.source_papers
    THEN n.source_papers + "{_escape(doi)}"
    ELSE n.source_papers
  END
"""
    else:
        print(f"  [WARN] Unknown label: {label}", file=sys.stderr)
        return False

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


def load_json(json_path):
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
    registry = load_existing_nodes()

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
            is_new = True
            print(f"  [NEW]   {entity['label']:10} {entity['canonical_name']}")
        elif action == "fuzzy_match":
            stats["fuzzy_flagged"] += 1
            stats["matched"] += 1
            is_new = False
            print(f"  [FUZZY] {entity['label']:10} {entity['canonical_name']} -> {resolved_name}")
        else:
            stats["matched"] += 1
            is_new = False
            print(f"  [MATCH] {entity['label']:10} {entity['canonical_name']} ({action})")

        # Update entity canonical_name to resolved name
        entity["canonical_name"] = resolved_name

        # MERGE node
        success = merge_node(entity, doi, json_filename, is_new)
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
        "--init-schema",
        action="store_true",
        help="Create constraints and fulltext indexes",
    )
    args = parser.parse_args()

    if not args.init_schema and not args.json_files:
        parser.print_help()
        sys.exit(1)

    if args.init_schema:
        init_schema()

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
            stats = load_json(json_path)
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

    # Exit with error if any failures
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
