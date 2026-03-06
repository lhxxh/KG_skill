---
name: kg-extract
description: Extract entities and relationships from a PDF paper using a provided schema, outputting structured JSON to output/. Triggered when extracting entities from a paper, running kg-extract, or called programmatically by the kg-pipeline skill. This skill uses the LLM to understand paper content and normalize entities. It does NOT touch Neo4j.
---

# Entity Extraction Guide

## Overview

This guide covers extracting entities and relationships from papers into structured JSON. Given a user-provided schema and a PDF, it produces a normalized JSON file in `output/`. This skill does NOT write to Neo4j — loading is handled by `.claude/skills/kg-pipeline/scripts/load_graph.py`.

## Four-Stage Pipeline

### Stage 1: Read the Schema

Read the user-provided schema file to understand what to extract. The schema defines:
- Node types (labels) and their properties
- Relationships between node types
- Any normalization or naming conventions

Do not assume a specific schema format — just read it and identify the node types, properties, and relationships it describes.

### Stage 2: Read the PDF

Use both text extraction and image conversion to capture all content from the PDF. See the pdf skill for detailed usage.

- **Text and tables**: use `pdfplumber` to extract text and tabular data from each page
- **Figures and complex layouts**: use `pdf2image` to convert pages to images for visual inspection

Also extract paper metadata (title, DOI, authors, year) from the first pages.

### Stage 3: Extract Entities and Relationships

Using the schema from Stage 1 and the paper content from Stage 2:

- For each node type in the schema, identify matching entities in the paper
- Normalize entity names to a consistent `canonical_name` so that entities referring to the same concept (e.g., a brand name vs. generic name, abbreviations vs. full names) share the same identifier — this is critical for downstream node merging across papers
- Identify relationships between entities as defined in the schema
- Assign each entity a unique `entity_id` (e1, e2, ...) and an `extraction_confidence` ("high", "medium", "low")
- Serialize any object/map-type properties as JSON strings (Neo4j does not support map properties)

### Stage 4: Write Output JSON

Write the extraction result to `output/{pdf_stem}_extraction.json`:

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
      "target_entity_id": "e2",
      "source_paper": "paper title"
    }
  ]
}
```

### Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source_paper.title` | string | yes | Full paper title |
| `source_paper.doi` | string | no | DOI string (if available) |
| `source_paper.authors` | string[] | yes | Author names |
| `source_paper.year` | int | yes | Publication year |
| `entities[].entity_id` | string | yes | Unique within file (e1, e2, ...) |
| `entities[].label` | string | yes | Must match a node type from the schema |
| `entities[].canonical_name` | string | yes | Normalized per schema conventions |
| `entities[].properties` | object | yes | Matches schema properties for that label |
| `entities[].extraction_confidence` | string | yes | high, medium, or low |
| `relationships[].type` | string | yes | Must match a relationship type from the schema |
| `relationships[].source_entity_id` | string | yes | References an entity_id |
| `relationships[].target_entity_id` | string | yes | References an entity_id |
| `relationships[].source_paper` | string | yes | Full title of the paper being processed |

## Important Rules

- NEVER write to Neo4j — extraction only
- Read the schema first to understand what to extract
- Use both pdfplumber (text/tables) and pdf2image (figures/complex layouts) — see pdf skill for details
- Serialize object/map-type properties as JSON strings
- Include ALL relevant entities described in the paper
