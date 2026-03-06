---
name: kg-pipeline
description: Orchestrates the full knowledge graph ingestion pipeline. Process papers from PDF to Neo4j by running extraction (LLM) and loading (deterministic). Triggered by "process papers", "ingest papers", "load papers into Neo4j", "run kg pipeline", "run dedup", or "process [specific PDF]". The user interacts with this skill — they never need to run scripts directly.
---

# Knowledge Graph Ingestion Pipeline

## Overview

This guide covers the end-to-end pipeline for ingesting papers into a Neo4j knowledge graph. The pipeline has two stages: LLM-powered extraction (PDF → JSON) and deterministic loading (JSON → Neo4j). The schema file defines what entities and relationships to extract. The user interacts with this skill — they never need to run scripts directly. For extraction details, see the kg-extract skill. For query operations, see the kg-qa skill.

## Architecture

```
User Request
  └─ kg-pipeline skill (this file)
       └─ python3 scripts/ingest.py --schema schema/X.md [args]
            ├─ Step 1: claude -p "kg-extract" (LLM extraction)
            │    └─ Reads PDF + schema → writes output/*.json
            └─ Step 2: python3 scripts/load_graph.py (deterministic)
                 └─ Entity resolution + MERGE into Neo4j
```

## Quick Start

```bash
# Process all PDFs in paper/ using the default schema
python3 scripts/ingest.py

# Process with a specific schema
python3 scripts/ingest.py --schema schema/my_schema.md
```

## Pipeline Commands

### Full Pipeline — All Papers

```bash
python3 scripts/ingest.py
python3 scripts/ingest.py --schema schema/my_schema.md
```

This will:
1. Init schema (constraints + indexes, idempotent via `IF NOT EXISTS`)
2. For each PDF in `paper/`: extract entities via kg-extract skill, then load into Neo4j
3. Skip PDFs that already have extraction JSONs in `output/`

### Full Pipeline — Specific Paper

```bash
python3 scripts/ingest.py paper/specific_paper.pdf
python3 scripts/ingest.py --schema schema/my_schema.md paper/specific_paper.pdf
```

### Extraction Only — Skip Neo4j Loading

```bash
python3 scripts/ingest.py --skip-loading
```

### Loading Only — Skip Extraction

```bash
python3 scripts/ingest.py --skip-extraction
```

Loads all existing `output/*_extraction.json` files without re-running Claude.

### Schema Only — Create Constraints and Indexes

```bash
python3 scripts/ingest.py --init-schema
```

Creates uniqueness constraints and fulltext indexes as defined in the schema (idempotent).

## Graph Inspection

### Node Summary

```bash
cypher-shell -u neo4j -p docling-graph \
  "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total ORDER BY total DESC;"
```

### Relationship Summary

```bash
cypher-shell -u neo4j -p docling-graph \
  "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS total ORDER BY total DESC;"
```

### Edge Provenance Check

```bash
cypher-shell -u neo4j -p docling-graph \
  "MATCH ()-[r]->() RETURN type(r), r.source_papers, r.extraction_source LIMIT 10;"
```

### Dedup Candidates

```bash
cypher-shell -u neo4j -p docling-graph \
  "MATCH (n) WHERE n.dedup_candidate = true RETURN labels(n)[0] AS label, n.canonical_name, n.dedup_reason;"
```

### Multi-Paper Entities

```bash
cypher-shell -u neo4j -p docling-graph \
  "MATCH (n) WHERE size(n.source_papers) > 1 RETURN labels(n)[0] AS label, n.canonical_name, n.source_papers;"
```

### Clear Database

```bash
cypher-shell -u neo4j -p docling-graph "MATCH (n) DETACH DELETE n;"
```

## Monitoring Progress

When running `ingest.py`, the output reports:
- Per-paper status: extraction started/completed, loading started/completed
- Entity resolution results: how many matched vs created vs fuzzy-flagged
- Final summary table: files succeeded/failed, total nodes created/merged, relationships

## Key Files

| File | Purpose |
|------|---------|
| `scripts/ingest.py` | Batch orchestrator (chains extraction + loading) |
| `scripts/load_graph.py` | Deterministic Neo4j loader with entity resolution |
| `.claude/skills/kg-extract/SKILL.md` | LLM extraction skill (PDF → JSON) |
| `.claude/skills/kg-qa/SKILL.md` | NL question answering skill (Cypher queries) |
| `schema/` | Schema files defining node types, relationships, constraints |
| `output/` | Extraction JSON files (contract between extraction and loading) |
| `paper/` | Source PDF papers |

## Quick Reference

| User says... | Command |
|--------------|---------|
| "Process papers" / "Ingest papers" | `python3 scripts/ingest.py` |
| "Process [specific PDF]" | `python3 scripts/ingest.py paper/file.pdf` |
| "Use schema X" | `python3 scripts/ingest.py --schema schema/X.md` |
| "Load existing extractions" | `python3 scripts/ingest.py --skip-extraction` |
| "Extract only" | `python3 scripts/ingest.py --skip-loading` |
| "Init schema" / "Create constraints" | `python3 scripts/ingest.py --init-schema` |
| "Run dedup" / "Check dedup candidates" | Query `WHERE n.dedup_candidate = true` |
| "Show graph summary" | Node + relationship count queries |
| "Check edge provenance" | Query `r.source_papers` on relationships |
| "Clear database" | `MATCH (n) DETACH DELETE n` |
