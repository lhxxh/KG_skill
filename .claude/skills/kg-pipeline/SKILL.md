# kg-pipeline — Knowledge Graph Ingestion Pipeline

## Trigger
- "process papers in paper/"
- "ingest papers"
- "load papers into Neo4j"
- "run kg pipeline"
- "run dedup"
- "process [specific PDF]"

## Purpose
User-facing meta skill that orchestrates the full KG pipeline. The user interacts with this skill — they never need to run scripts directly.

## Architecture
```
User Request
  └─ kg-pipeline skill (this file)
       └─ python3 scripts/ingest.py [args]
            ├─ Step 1: claude -p "kg-extract" (LLM extraction)
            │    └─ Reads PDF + schema → writes output/*.json
            └─ Step 2: python3 scripts/load_graph.py (deterministic)
                 └─ Entity resolution + MERGE into Neo4j
```

## How to Handle Requests

### "Process papers in paper/" (or similar)
Run the full pipeline on all PDFs:
```bash
python3 scripts/ingest.py
```
This will:
1. Init schema (constraints + indexes, idempotent)
2. For each PDF in `paper/`: extract entities via kg-extract skill → load into Neo4j
3. Skip PDFs that already have extraction JSONs in `output/`

### "Process [specific PDF]"
```bash
python3 scripts/ingest.py paper/specific_paper.pdf
```

### "Load existing extractions" / "Skip extraction"
Load already-extracted JSONs without re-running Claude:
```bash
python3 scripts/ingest.py --skip-extraction
```

### "Extract only" / "Don't load into Neo4j"
Run extraction without loading:
```bash
python3 scripts/ingest.py --skip-loading
```

### "Init schema" / "Create constraints"
```bash
python3 scripts/ingest.py --init-schema
```

### "Run dedup" / "Check dedup candidates"
Query for nodes flagged during entity resolution:
```bash
cypher-shell -u neo4j -p docling-graph "MATCH (n) WHERE n.dedup_candidate = true RETURN labels(n)[0] AS label, n.canonical_name, n.dedup_reason;"
```

### "Show graph summary"
```bash
cypher-shell -u neo4j -p docling-graph "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total ORDER BY total DESC;"
cypher-shell -u neo4j -p docling-graph "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS total ORDER BY total DESC;"
```

### "Check edge provenance"
```bash
cypher-shell -u neo4j -p docling-graph "MATCH ()-[r]->() RETURN type(r), r.source_papers, r.extraction_source LIMIT 10;"
```

## Monitoring Progress
When running `ingest.py`, monitor the output and report to the user:
- Per-paper status: extraction started/completed, loading started/completed
- Entity resolution results: how many matched vs created vs fuzzy-flagged
- Final summary: total nodes created/merged, relationships, failures

## Key Files
- `scripts/ingest.py` — batch orchestrator (calls extraction + loading)
- `scripts/load_graph.py` — deterministic Neo4j loader with entity resolution
- `.claude/skills/kg-extract/SKILL.md` — LLM extraction skill (PDF → JSON)
- `schema/pk_schema.md` — node types, relationships, naming conventions
- `output/` — extraction JSON files (contract between extraction and loading)

## Schema Help
Point users to `schema/pk_schema.md` for:
- Node type definitions and property types
- Naming conventions (INN for drugs, snake_case for types/diseases)
- Relationship types and cardinality
- Constraint and index definitions

For schema authoring guidance, the format is documented in `schema/pk_schema.md` itself.
