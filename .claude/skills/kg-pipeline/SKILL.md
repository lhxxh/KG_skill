---
name: kg-pipeline
description: Orchestrates the full knowledge graph ingestion pipeline. Process papers from PDF to Neo4j by running extraction (LLM) and loading (deterministic). Triggered by "process papers", "ingest papers", "load papers into Neo4j", "run kg pipeline", or "process [specific PDF]". The user interacts with this skill — they never need to run scripts directly.
---

# Knowledge Graph Ingestion Pipeline

## Overview

This guide covers building a knowledge graph from papers and loading it into Neo4j. The pipeline is driven by `ingest.py`, which iterates over PDF files, launches a Claude instance per file for extraction, then loads the resulting JSON into Neo4j with a deterministic script. For extraction details, see the kg-extract skill. For querying, see the kg-qa skill.

## Running the Pipeline

Run `ingest.py` with two arguments: the paper(s) and the schema.

```bash
# Process all PDFs in a directory
python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/ schema/pk_schema.md

# Process a single PDF
python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/specific.pdf schema/my_schema.md
```

## How `ingest.py` Works

`ingest.py` runs in two phases:

**Phase 1: Parallel Extraction (LLM)** — all PDFs are extracted concurrently. For each PDF:
1. Skip if `output/{pdf_stem}_extraction.json` already exists
2. Launch a Claude process with the kg-extract skill:
   ```bash
   claude -p "Extract all entities and relationships from the paper at <pdf> using the schema at <schema>. Write the extraction JSON to output/<stem>_extraction.json. Follow the kg-extract skill instructions exactly." --dangerously-skip-permissions
   ```
3. Validate the output JSON contains `source_paper`, `entities`, and `relationships`

**Phase 2: Sequential Loading (deterministic, no LLM)** — after all extractions finish, each JSON is loaded one at a time:
   ```bash
   python3 .claude/skills/kg-pipeline/scripts/load_graph.py output/<stem>_extraction.json
   ```
   `load_graph.py` reads the JSON, derives node labels from the entities, performs entity resolution via a 3-step cascade (exact → case-insensitive → alias → create new), and MERGEs nodes and relationships into Neo4j via `cypher-shell`. Every node and relationship tracks provenance (which paper(s) asserted it). Loading is sequential because entity resolution depends on what was already loaded.

Failure on one paper does not abort others. A summary is printed at the end.

Neo4j connection settings are read from `.claude/settings.json` under `mcpServers.neo4j.env`, overridable via env vars `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.

## Scripts

Both scripts live under `.claude/skills/kg-pipeline/scripts/`:

| Script | Purpose |
|--------|---------|
| `ingest.py` | Batch orchestrator — iterates PDFs, launches Claude for extraction, calls `load_graph.py` for loading |
| `load_graph.py` | Deterministic Neo4j loader — reads extraction JSON, entity resolution, MERGE nodes/relationships, provenance tracking |

## Quick Reference

| User says... | What to run |
|--------------|-------------|
| "Process papers in paper/" | `python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/ schema/X.md` |
| "Process [specific PDF]" | `python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/file.pdf schema/X.md` |
