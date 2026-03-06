---
name: kg-extract
description: Extract pharmacokinetic entities and relationships from a PDF paper using the PK schema, outputting structured JSON to output/. Triggered when extracting entities from a paper, running kg-extract, or called programmatically by scripts/ingest.py. This skill uses the LLM to understand paper content and normalize entities. It does NOT touch Neo4j.
---

# PK Entity Extraction Guide

## Overview

This guide covers extracting pharmacokinetic entities and relationships from scientific papers into structured JSON. The extraction reads a PDF and the PK schema (`schema/pk_schema.md`), then writes a normalized JSON file to `output/`. For PDF text and table extraction operations, see the pdf skill. This skill does NOT write to Neo4j — loading is handled by `scripts/load_graph.py`.

## Quick Start

```python
import pdfplumber
import json
from pathlib import Path

# 1. Read schema
schema = Path("schema/pk_schema.md").read_text()

# 2. Extract text from PDF
with pdfplumber.open("paper/s40262-016-0505-1.pdf") as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n\n".join(pages)

# 3. Write extraction JSON
output = {
    "source_paper": {"title": "...", "doi": "...", "authors": ["..."], "year": 2017},
    "entities": [...],
    "relationships": [...]
}
Path("output/s40262-016-0505-1_extraction.json").write_text(json.dumps(output, indent=2))
```

## PDF Text Extraction

### pdfplumber — Text with Layout

```python
import pdfplumber

with pdfplumber.open(pdf_path) as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n\n".join(pages)
```

### pdfplumber — Parameter Tables

```python
with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for table in tables:
            # Look for PK parameter tables (CL, V, Ka, etc.)
            print(f"Table on page {i+1}: {table}")
```

### pdf2image — Figures and Complex Tables

For pages where pdfplumber misses tabular data (parameter tables, PK plots):

```python
from pdf2image import convert_from_path

# Convert specific pages to images for visual inspection
images = convert_from_path(pdf_path, first_page=5, last_page=8)
for i, img in enumerate(images):
    img.save(f"page_{i+5}.png")
```

## Paper Metadata

Extract from the first 1–2 pages:

| Field | Type | Example |
|-------|------|---------|
| `title` | string | "Target-Mediated Drug Disposition Population PK Model..." |
| `doi` | string | "10.1007/s40262-016-0505-1" |
| `authors` | string[] | ["Djebli N", "Martinez JM", "Lohan L"] |
| `year` | int | 2017 |

## Entity Normalization Rules

### Drug — INN/generic, lowercase

Map brand names to INN generics. Record originals as aliases.

| Paper text | `canonical_name` | `aliases` |
|------------|-------------------|-----------|
| "Humira" | `adalimumab` | `["humira"]` |
| "Praluent (alirocumab)" | `alirocumab` | `["praluent"]` |
| "warfarin sodium" | `warfarin` | `[]` |

Properties: `drug_name` (same as canonical), `drug_type` (e.g., `"monoclonal_antibody"`, `"small_molecule"`), `aliases` (string[]).

### Type — snake_case model structure

| Paper text | `canonical_name` |
|------------|-------------------|
| "two-compartment TMDD QSS" | `two_compartment_tmdd_qss` |
| "1-compartment linear" | `one_compartment_linear` |
| "nonlinear Michaelis-Menten" | `nonlinear_michaelis_menten` |

Properties: `model_type` (same as canonical).

### Organism — lowercase common name

| Paper text | `canonical_name` |
|------------|-------------------|
| "healthy volunteers" | `human` |
| "Sprague-Dawley rats" | `rat` |
| "cynomolgus monkeys" | `monkey` |

Properties: `organism` (same as canonical).

### Disease — snake_case

| Paper text | `canonical_name` | `aliases` |
|------------|-------------------|-----------|
| "familial hypercholesterolemia" | `familial_hypercholesterolemia` | `["FH"]` |
| "rheumatoid arthritis" | `rheumatoid_arthritis` | `["RA"]` |

Properties: `name` (human-readable), `aliases` (string[]).

### Model — `{drug}_{descriptor}_{type}` format

| Drug | Descriptor | Type | `canonical_name` |
|------|-----------|------|-------------------|
| alirocumab | popPK | two_compartment_tmdd_qss | `alirocumab_popPK_two_compartment_tmdd_qss` |
| rituximab | sc_fcRn | multi_compartment_fcRn | `rituximab_sc_fcRn_multi_compartment_fcRn` |

Properties:
- `mathematical_equations`: string[] — core PK equations (native array)
- `parameter_means`: string — JSON-serialized object (e.g., `'{"CL": 0.0135, "V1": 3.5}'`)
- `parameter_iiv_std_dev`: string — JSON-serialized object (e.g., `'{"CL_iiv_cv%": 42}'`)

## Relationships

All relationships have a Model as the source node:

| Type | Source | Target | Cardinality |
|------|--------|--------|-------------|
| `IS_TYPE` | Model | Type | one-to-one |
| `CHARACTERIZES` | Model | Drug | many-to-one |
| `STUDIED_IN` | Model | Organism | many-to-many |
| `TREATS` | Model | Disease | many-to-many |

## JSON Output Contract

Write to `output/{pdf_stem}_extraction.json`:

```json
{
  "source_paper": {
    "title": "Target-Mediated Drug Disposition Population PK Model of Alirocumab...",
    "doi": "10.1007/s40262-016-0505-1",
    "authors": ["Djebli N", "Martinez JM", "Lohan L"],
    "year": 2017
  },
  "entities": [
    {
      "entity_id": "e1",
      "label": "Drug",
      "canonical_name": "alirocumab",
      "properties": {
        "drug_name": "alirocumab",
        "drug_type": "monoclonal_antibody",
        "aliases": ["praluent"]
      },
      "extraction_confidence": "high"
    },
    {
      "entity_id": "e2",
      "label": "Type",
      "canonical_name": "two_compartment_tmdd_qss",
      "properties": {
        "model_type": "two_compartment_tmdd_qss"
      },
      "extraction_confidence": "high"
    },
    {
      "entity_id": "e6",
      "label": "Model",
      "canonical_name": "alirocumab_popPK_two_compartment_tmdd_qss",
      "properties": {
        "mathematical_equations": ["dC/dt = Ka*Adepot/Vc - (CLL/Vc)*C - Kint*RC"],
        "parameter_means": "{\"CL\": 0.176, \"Vc\": 4.67}",
        "parameter_iiv_std_dev": "{\"CL_cv_pct\": 43.9}"
      },
      "extraction_confidence": "high"
    }
  ],
  "relationships": [
    {
      "type": "IS_TYPE",
      "source_entity_id": "e6",
      "target_entity_id": "e2"
    },
    {
      "type": "CHARACTERIZES",
      "source_entity_id": "e6",
      "target_entity_id": "e1"
    }
  ]
}
```

### Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source_paper.title` | string | yes | Full paper title |
| `source_paper.doi` | string | yes | DOI string |
| `source_paper.authors` | string[] | yes | Abbreviated format |
| `source_paper.year` | int | yes | Publication year |
| `entities[].entity_id` | string | yes | Unique within file (e1, e2, ...) |
| `entities[].label` | string | yes | One of: Drug, Type, Organism, Disease, Model |
| `entities[].canonical_name` | string | yes | Normalized per rules above |
| `entities[].properties` | object | yes | Matches schema for that label |
| `entities[].extraction_confidence` | string | yes | high, medium, or low |
| `relationships[].type` | string | yes | One of: IS_TYPE, CHARACTERIZES, STUDIED_IN, TREATS |
| `relationships[].source_entity_id` | string | yes | Always a Model |
| `relationships[].target_entity_id` | string | yes | References an entity_id |

## Important Rules

- NEVER write to Neo4j — extraction only
- Serialize `parameter_means` and `parameter_iiv_std_dev` as JSON strings, not objects
- `mathematical_equations` should be a native array of strings
- Include ALL models described in the paper, even if they reference the same drug
- For multi-drug papers (e.g., mAb review papers), extract ALL drugs with their own models
- Assign entity_ids sequentially (e1, e2, ...) — order does not matter as long as relationships reference valid ids

## Quick Reference

| Task | Approach |
|------|----------|
| Extract text | `pdfplumber.open(path)` → `page.extract_text()` |
| Extract tables | `pdfplumber` → `page.extract_tables()` |
| Inspect figures | `pdf2image` → convert pages to images |
| Normalize drug name | Map to INN/generic, lowercase |
| Normalize type | Convert to snake_case |
| Normalize disease | Convert to snake_case |
| Normalize organism | Lowercase common name |
| Construct model name | `{drug}_{descriptor}_{type}` |
| Serialize map properties | `json.dumps(dict)` → store as string |
