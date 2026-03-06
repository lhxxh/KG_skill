---
name: kg-extract
description: Extract entities and relationships from a PDF paper using a provided schema, outputting structured JSON to output/. Triggered when extracting entities from a paper, running kg-extract, or called programmatically by scripts/ingest.py. This skill uses the LLM to understand paper content and normalize entities. It does NOT touch Neo4j.
---

# Entity Extraction Guide

## Overview

This guide covers extracting entities and relationships from scientific papers into structured JSON. The extraction reads a PDF and a schema file (e.g., `schema/pk_schema.md`), then writes a normalized JSON file to `output/`. The schema defines what node types, properties, and relationships to look for. For PDF text and table extraction operations, see the pdf skill. This skill does NOT write to Neo4j — loading is handled by `scripts/load_graph.py`.

## Quick Start

```python
import pdfplumber
import json
from pathlib import Path

# 1. Read schema to understand what entities/relationships to extract
schema = Path("schema/pk_schema.md").read_text()

# 2. Extract text from PDF
with pdfplumber.open("paper/example.pdf") as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n\n".join(pages)

# 3. Write extraction JSON
output = {
    "source_paper": {"title": "...", "doi": "...", "authors": ["..."], "year": 2017},
    "entities": [...],
    "relationships": [...]
}
Path("output/example_extraction.json").write_text(json.dumps(output, indent=2))
```

## PDF Text Extraction

### pdfplumber — Text with Layout

```python
import pdfplumber

with pdfplumber.open(pdf_path) as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n\n".join(pages)
```

### pdfplumber — Tables

```python
with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for table in tables:
            print(f"Table on page {i+1}: {table}")
```

### pdf2image — Figures and Complex Tables

For pages where pdfplumber misses tabular data:

```python
from pdf2image import convert_from_path

images = convert_from_path(pdf_path, first_page=5, last_page=8)
for i, img in enumerate(images):
    img.save(f"page_{i+5}.png")
```

## Paper Metadata

Extract from the first 1–2 pages:

| Field | Type | Example |
|-------|------|---------|
| `title` | string | "Full paper title..." |
| `doi` | string | "10.xxxx/xxxxx" |
| `authors` | string[] | ["Author A", "Author B"] |
| `year` | int | 2017 |

## Entity Normalization

Read the schema to understand normalization rules for each node type. The schema uses the format:

```
**Label** (description)
- `canonical_name`: string [canonical] — description → **normalization rule**
- `property_name`: type — description
```

Apply the normalization rules specified after the `→` arrow. Common patterns:
- **lowercase** — e.g., `"human"`, `"warfarin"`
- **snake_case** — e.g., `"rheumatoid_arthritis"`, `"two_compartment"`
- **canonical/generic name** — e.g., brand name "Humira" → generic "adalimumab"
- **JSON string** for `object` type properties — serialize dicts as JSON strings since Neo4j doesn't support map properties

Assign each entity a unique `entity_id` (e1, e2, ...) and an `extraction_confidence` ("high", "medium", "low").

## Relationships

Read the schema's `### Relationships` section to understand which entity types connect and how. Relationships always reference entities by their `entity_id`. The schema format:

```
- `(SourceLabel)-[:REL_TYPE]->(TargetLabel)` — description — **cardinality**
```

## JSON Output Contract

Write to `output/{pdf_stem}_extraction.json`:

```json
{
  "source_paper": {
    "title": "Full paper title...",
    "doi": "10.xxxx/xxxxx",
    "authors": ["Author A", "Author B"],
    "year": 2017
  },
  "entities": [
    {
      "entity_id": "e1",
      "label": "SomeLabel",
      "canonical_name": "normalized_name",
      "properties": {
        "prop1": "value1",
        "prop2": ["array", "value"]
      },
      "extraction_confidence": "high"
    }
  ],
  "relationships": [
    {
      "type": "REL_TYPE",
      "source_entity_id": "e1",
      "target_entity_id": "e2"
    }
  ]
}
```

### Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source_paper.title` | string | yes | Full paper title |
| `source_paper.doi` | string | yes | DOI string |
| `source_paper.authors` | string[] | yes | Author names |
| `source_paper.year` | int | yes | Publication year |
| `entities[].entity_id` | string | yes | Unique within file (e1, e2, ...) |
| `entities[].label` | string | yes | Must match a label from the schema |
| `entities[].canonical_name` | string | yes | Normalized per schema rules |
| `entities[].properties` | object | yes | Matches schema properties for that label |
| `entities[].extraction_confidence` | string | yes | high, medium, or low |
| `relationships[].type` | string | yes | Must match a relationship type from the schema |
| `relationships[].source_entity_id` | string | yes | References an entity_id |
| `relationships[].target_entity_id` | string | yes | References an entity_id |

## Important Rules

- NEVER write to Neo4j — extraction only
- Read the schema file first to understand what to extract
- Apply normalization rules from the schema (after the `→` arrow)
- Serialize `object` type properties as JSON strings, not dicts
- `string[]` type properties should be native arrays of strings
- Include ALL relevant entities described in the paper
- Assign entity_ids sequentially (e1, e2, ...) — order does not matter as long as relationships reference valid ids

## Quick Reference

| Task | Approach |
|------|----------|
| Extract text | `pdfplumber.open(path)` → `page.extract_text()` |
| Extract tables | `pdfplumber` → `page.extract_tables()` |
| Inspect figures | `pdf2image` → convert pages to images |
| Understand what to extract | Read the schema file |
| Normalize entities | Follow `→` rules in the schema |
| Serialize object properties | `json.dumps(dict)` → store as string |
| Determine relationships | Read `### Relationships` in the schema |
