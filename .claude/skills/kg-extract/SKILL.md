# kg-extract — PDF to Structured JSON Extraction

## Trigger
- "extract entities from [PDF]"
- "run kg-extract on [PDF]"
- Called programmatically by `scripts/ingest.py`

## Purpose
Read a PDF paper + the PK schema → output a structured extraction JSON to `output/`. This skill uses the LLM to understand paper content and normalize entities. It does NOT touch Neo4j.

## Inputs
- A PDF file path (e.g., `paper/s40262-016-0505-1.pdf`)
- Schema at `schema/pk_schema.md`

## Output
- JSON file at `output/{pdf_stem}_extraction.json`

## Extraction Flow

### Step 1: Read Schema
Read `schema/pk_schema.md` to understand:
- Node types: Model, Type, Drug, Organism, Disease
- Property definitions and normalization rules (INN for drugs, snake_case for types/diseases)
- Relationship types: IS_TYPE, CHARACTERIZES, STUDIED_IN, TREATS
- Canonical name formats (e.g., `{drug}_{descriptor}_{type}` for Model)

### Step 2: Extract PDF Text
Use pdfplumber (from pdf skill patterns) to extract text from all pages:
```python
import pdfplumber
with pdfplumber.open(pdf_path) as pdf:
    pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n\n".join(pages)
```

### Step 3: Extract Tables and Figures
For pages where pdfplumber text extraction may miss tabular data (parameter tables, PK results):
- Use `pdf2image` to convert key pages (typically methods/results sections) to images
- Visually inspect tables for parameter values, IIV estimates, study populations

### Step 4: Extract Paper Metadata
From the first 1-2 pages, identify:
- `title`: full paper title
- `doi`: DOI string (e.g., "10.1007/s40262-016-0505-1")
- `authors`: list of author names (abbreviated format, e.g., "Djebli N")
- `year`: publication year (integer)

### Step 5: Extract Entities
For each schema node type, scan the full text and identify matching entities:

**Drug**: Identify drug names → normalize to INN/generic lowercase. Map brand names to generics (e.g., "Humira" → "adalimumab", "Praluent" → "alirocumab"). Record `drug_type` and `aliases`.

**Type**: Identify model structure → normalize to snake_case (e.g., "two-compartment TMDD QSS" → "two_compartment_tmdd_qss").

**Organism**: Identify species → normalize to lowercase common name (e.g., "healthy volunteers" → "human").

**Disease**: Identify indications → normalize to snake_case (e.g., "familial hypercholesterolemia" → "familial_hypercholesterolemia"). Record aliases.

**Model**: Construct from context:
- `canonical_name`: `{drug}_{descriptor}_{type}` format (e.g., "alirocumab_popPK_two_compartment_tmdd_qss")
- `mathematical_equations`: core PK equations as string array
- `parameter_means`: population parameter estimates as JSON string
- `parameter_iiv_std_dev`: IIV (inter-individual variability) as JSON string

Assign each entity a unique `entity_id` (e1, e2, ...) and an `extraction_confidence` ("high", "medium", "low").

### Step 6: Extract Relationships
For each schema relationship type, identify connections using entity_ids:
- `(Model)-[:IS_TYPE]->(Type)` — one per model
- `(Model)-[:CHARACTERIZES]->(Drug)` — one per model
- `(Model)-[:STUDIED_IN]->(Organism)` — one or more per model
- `(Model)-[:TREATS]->(Disease)` — one or more per model

### Step 7: Write Output JSON
Write the extraction to `output/{pdf_stem}_extraction.json`:

```json
{
  "source_paper": {
    "title": "...",
    "doi": "...",
    "authors": ["..."],
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
    }
  ],
  "relationships": [
    {
      "type": "IS_TYPE",
      "source_entity_id": "e6",
      "target_entity_id": "e2"
    }
  ]
}
```

## JSON Contract
The output JSON MUST conform to this structure:
- `source_paper`: object with `title` (string), `doi` (string), `authors` (string[]), `year` (int)
- `entities`: array of objects, each with:
  - `entity_id`: string (e.g., "e1")
  - `label`: one of "Drug", "Type", "Organism", "Disease", "Model"
  - `canonical_name`: string, normalized per schema rules
  - `properties`: object matching schema properties for that label
  - `extraction_confidence`: "high" | "medium" | "low"
- `relationships`: array of objects, each with:
  - `type`: one of "IS_TYPE", "CHARACTERIZES", "STUDIED_IN", "TREATS"
  - `source_entity_id`: string referencing an entity_id (always a Model)
  - `target_entity_id`: string referencing an entity_id

## Important Rules
- NEVER write to Neo4j — this is extraction only
- Always normalize drug names to INN/generic (lowercase)
- Always normalize types and diseases to snake_case
- Model canonical_name must follow `{drug}_{descriptor}_{type}` format
- Serialize parameter_means and parameter_iiv_std_dev as JSON strings, not objects
- mathematical_equations should be a native array of strings
- Include ALL models described in the paper, even if they reference the same drug
- For multi-drug papers (e.g., mAb review papers), extract ALL drugs mentioned with their own models
