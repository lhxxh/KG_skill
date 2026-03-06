#!/usr/bin/env python3
"""
load_graph.py — Deterministic Neo4j loader (NO LLM)

Reads extraction JSON files and loads entities/relationships into Neo4j
using cypher-shell subprocess calls. Derives node labels from the JSON
entities and performs entity resolution via a 3-step cascade
(exact, case-insensitive, alias, or create new).

Usage:
    python3 .claude/skills/kg-pipeline/scripts/load_graph.py output/paper1_extraction.json
    python3 .claude/skills/kg-pipeline/scripts/load_graph.py output/*.json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# --- Configuration ---

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASSWORD", "docling-graph")

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
    3-step entity resolution cascade.
    Returns (resolved_name, action) where action is one of:
    'exact_match', 'case_match', 'alias_match', 'create_new'
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

    # Step 4: Create new
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


def merge_node(entity, paper_title):
    """MERGE a single node into Neo4j. Reads label and properties from the entity."""
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
    create_sets.append(f'  n.source_papers = ["{_escape(paper_title)}"]')
    create_sets.append(f"  n.created_at = datetime()")

    create_clause = ",\n".join(create_sets)

    query = f"""
MERGE (n:{label} {{canonical_name: "{_escape(name)}"}})
ON CREATE SET
{create_clause}
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT "{_escape(paper_title)}" IN n.source_papers
    THEN n.source_papers + "{_escape(paper_title)}"
    ELSE n.source_papers
  END
"""
    result = run_cypher(query)
    return result is not None


# --- Relationship MERGE ---


def merge_relationship(rel, entities_by_id, paper_title):
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
  r.source_papers = ["{_escape(paper_title)}"],
  r.created_at = datetime()
ON MATCH SET
  r.source_papers = CASE
    WHEN NOT "{_escape(paper_title)}" IN r.source_papers
    THEN r.source_papers + "{_escape(paper_title)}"
    ELSE r.source_papers
  END
"""
    result = run_cypher(query)
    return result is not None


# --- Main Loading Logic ---


def load_json(json_path):
    """Load a single extraction JSON into Neo4j."""
    json_path = Path(json_path)
    print(f"=== Loading: {json_path.name} ===")

    with open(json_path) as f:
        data = json.load(f)

    source_paper = data["source_paper"]
    paper_title = source_paper["title"]
    entities = data["entities"]
    relationships = data["relationships"]

    print(f"  Paper: {paper_title[:80]}...")
    print(f"  DOI: {source_paper.get('doi', 'N/A')}")
    print(f"  Entities: {len(entities)}, Relationships: {len(relationships)}")

    # Derive labels from entities in this JSON
    labels = list({e["label"] for e in entities})

    # Build entity lookup by id
    entities_by_id = {e["entity_id"]: e for e in entities}

    # Load existing nodes for entity resolution
    registry = load_existing_nodes(labels)

    # Stats
    stats = {
        "matched": 0,
        "created": 0,
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
        else:
            stats["matched"] += 1
            print(f"  [MATCH] {entity['label']:10} {entity['canonical_name']} ({action})")

        # Update entity canonical_name to resolved name
        entity["canonical_name"] = resolved_name

        # MERGE node
        success = merge_node(entity, paper_title)
        if not success:
            stats["merge_failed"] += 1

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

        success = merge_relationship(rel, entities_by_id, paper_title)
        if success:
            stats["rels_created"] += 1
        else:
            stats["rels_failed"] += 1

    # --- Phase 3: Verification ---
    print("\n  --- Verification ---")
    out = run_cypher(f"""
MATCH (n) WHERE "{_escape(paper_title)}" IN n.source_papers
RETURN labels(n)[0] AS label, count(n) AS count
""")
    if out:
        print(f"  Nodes for this paper:")
        for line in out.strip().split("\n")[1:]:
            if line.strip():
                print(f"    {line.strip()}")

    out = run_cypher(f"""
MATCH ()-[r]->() WHERE "{_escape(paper_title)}" IN r.source_papers
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

    # Edge provenance check — one sample per source paper
    print("\n  Edge provenance sample (per paper):")
    out = run_cypher("""
MATCH ()-[r]->()
UNWIND r.source_papers AS title
WITH title, collect(r)[0] AS r0
RETURN type(r0) AS rel, r0.source_papers AS papers, title
ORDER BY title
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
        nargs="+",
        help="Path(s) to extraction JSON files",
    )
    args = parser.parse_args()

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
    total_rels = sum(s["rels_created"] for _, s in all_stats)
    print(f"  Files loaded:  {len(all_stats)}")
    print(f"  Files failed:  {len(failed)}")
    print(f"  Nodes created: {total_created}")
    print(f"  Nodes matched: {total_matched}")
    print(f"  Relationships: {total_rels}")

    if failed:
        print(f"\n  Failed files:")
        for f in failed:
            print(f"    - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
