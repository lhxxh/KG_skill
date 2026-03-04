---
name: kg-pipeline
description: Orchestrates a full knowledge-graph pipeline for pharmacokinetic papers. Composes the pdf skill (text/table extraction) and neo4j-cypher skill (read/write Cypher) to extract entities and relationships from PDFs, resolve them against existing Neo4j nodes, and load them via the neo4j MCP server. Also supports batch deduplication and schema authoring.
---

# KG Pipeline — PDF to Knowledge Graph

This skill orchestrates the pipeline from PDF papers to a Neo4j knowledge graph. It **delegates** to two existing skills:

- **pdf skill** — for all PDF text and table extraction (pdfplumber, pypdf)
- **neo4j-cypher skill** — for all Cypher generation (read patterns from SKILL.md, write patterns from `references/write-patterns.md`)

The kg-pipeline adds: schema-guided knowledge extraction, entity resolution, and deduplication.

## Three Modes

| Mode | Trigger | What Happens |
|------|---------|-------------|
| **A: Process paper** | User gives a PDF path (+ optional schema path) | Full pipeline: extract → resolve → insert |
| **B: Dedup pass** | User says "run dedup" or "deduplicate" | Batch comparison and merge of candidate duplicates |
| **C: Schema help** | User asks how to write/modify a schema | Guide them using `schema-format.md` |

---

## Mode A: Process a Paper

**Input:** PDF file path. Optional schema path (defaults to `schema/pk_schema.md`).

Run these four stages in order.

### Stage 1 — PDF Content Extraction

**Delegate to the pdf skill.** Use a multi-layer extraction strategy to capture both text and visual content.

#### Layer 1: Text extraction (pdfplumber)

```python
import pdfplumber

with pdfplumber.open(pdf_path) as pdf:
    pages = []
    tables = []
    for i, page in enumerate(pdf.pages):
        pages.append({"page": i + 1, "text": page.extract_text() or ""})
        for table in page.extract_tables():
            tables.append({"page": i + 1, "rows": table})
```

Capture metadata from the first 2 pages:
- **title** — usually the largest text on page 1
- **DOI** — look for `doi:` or `https://doi.org/` patterns
- **authors** — names between title and abstract
- **year** — from DOI, header, or copyright line

#### Layer 2: Page images for visual inspection

PK papers contain critical info in figures (compartment diagrams, PK curves, parameter tables rendered as images). Convert pages to images using the pdf skill's `convert_pdf_to_images.py`:

```python
from pdf2image import convert_from_path

images = convert_from_path(pdf_path, dpi=200)
for i, image in enumerate(images):
    # Resize if needed for context window efficiency
    width, height = image.size
    max_dim = 1000
    if width > max_dim or height > max_dim:
        scale = min(max_dim / width, max_dim / height)
        image = image.resize((int(width * scale), int(height * scale)))
    image.save(f"output/{basename}_page_{i+1}.png")
```

Then visually read the page images with Claude to extract:
- **Parameter tables** that pdfplumber may miss (complex layouts, merged cells)
- **Compartment model diagrams** — identify model structure (1-cmt, 2-cmt, TMDD)
- **PK profile figures** — confirm drug names, dosing regimens, species
- **Equations rendered as images** — mathematical model definitions

#### Layer 3: OCR fallback for scanned PDFs

If pdfplumber returns little/no text (< 100 chars per page), fall back to OCR:

```python
import pytesseract
from pdf2image import convert_from_path

images = convert_from_path(pdf_path)
for i, image in enumerate(images):
    text = pytesseract.image_to_string(image)
    pages.append({"page": i + 1, "text": text})
```

#### Layer 4: Large PDF handling

For PDFs with many pages (textbooks, long reviews), process in chunks to manage context:

```python
from pypdf import PdfReader

reader = PdfReader(pdf_path)
total_pages = len(reader.pages)
chunk_size = 10  # Process 10 pages at a time

for start in range(0, total_pages, chunk_size):
    end = min(start + chunk_size, total_pages)
    # Extract text + images for pages start..end
    # Run Stage 2 extraction per chunk
    # Merge extracted entities across chunks (dedup by canonical_name)
```

#### Extraction priority

Use the layers in order — each adds information the previous may miss:

1. **Always run Layer 1** (text) — fast, gets most structured content
2. **Always run Layer 2** (images) on pages with figures/tables — catches visual-only information like diagrams and image-based tables
3. **Run Layer 3** (OCR) only if Layer 1 yields poor text output
4. **Run Layer 4** (chunking) only for PDFs with > 20 pages

### Stage 2 — Schema-Guided Knowledge Extraction

1. Read the schema file (see `schema-format.md` for the format).
2. For each **node type** in the schema, scan the paper text for matching entities.
3. For each **relationship type** in the schema, identify connections between extracted entities.
4. Apply normalization rules declared in the schema:
   - Lowercase all `canonical_name` values
   - Drug names → INN/generic form (e.g., "Humira" → "adalimumab")
   - Enum values → snake_case (e.g., "Two Compartment" → "two_compartment")
5. Assign confidence: `high` (explicitly stated), `medium` (inferred), `low` (uncertain).

Output the extraction result as JSON and **save it to `output/`**:

```json
{
  "source_paper": {
    "title": "Population PK of Adalimumab in RA Patients",
    "doi": "10.1234/example",
    "authors": ["Smith J", "Doe A"],
    "year": 2024
  },
  "entities": [
    {
      "entity_id": "e1",
      "label": "Drug",
      "canonical_name": "adalimumab",
      "properties": {"drug_name": "adalimumab", "drug_type": "monoclonal_antibody"},
      "extraction_confidence": "high"
    }
  ],
  "relationships": [
    {"type": "CHARACTERIZES", "source_entity_id": "e2", "target_entity_id": "e1"}
  ]
}
```

**Note:** Serialize object-valued properties (like `parameter_means`) as JSON strings — Neo4j does not support map properties.

### Stage 3 — Entity Resolution

For each extracted entity, check whether it already exists in Neo4j before creating it. Follow the 5-step lookup algorithm in `entity-resolution.md`:

1. Exact match on `canonical_name`
2. Case-insensitive match via `toLower()`
3. Alias array lookup
4. Fulltext index fuzzy search (score > 0.7)
5. Create new if no match

Execute lookup queries via `mcp__neo4j__query`. Build a resolution map:

```
Resolution Map:
  e1 (Drug: adalimumab)        → MATCHED existing (exact)
  e2 (Model: adalimumab_popPK) → CREATE NEW
  e3 (Type: two_compartment)   → MATCHED existing (case-insensitive)
```

For fuzzy matches scoring 0.7–0.9, flag as `dedup_candidate: true` for later review.

**Save the resolution map** to `output/` alongside the extraction JSON.

### Stage 4 — Cypher Generation & Execution

**Delegate Cypher generation to the neo4j-cypher skill.** Use the write patterns from `references/write-patterns.md`:

- MERGE nodes on `canonical_name` with `ON CREATE SET` / `ON MATCH SET`
- MERGE relationships with provenance tracking
- Execute each statement via `mcp__neo4j__query`
- Run verification queries to confirm insertion

### Intermediate Output Files

Save intermediate results to the `output/` directory (create it if it doesn't exist). Use the PDF filename (without extension) as the base name:

```
output/
  1-s2.0-S0378517323011092-main_extraction.json   # Stage 2: extracted entities & relationships
  1-s2.0-S0378517323011092-main_resolution.json    # Stage 3: resolution map & decisions
  1-s2.0-S0378517323011092-main_page_1.png         # Stage 1: page images (for visual inspection)
  1-s2.0-S0378517323011092-main_page_2.png
  ...
```

**`_extraction.json`** — the full extraction JSON from Stage 2 (source_paper metadata, entities, relationships).

**`_resolution.json`** — the resolution map from Stage 3, structured as:
```json
{
  "source_paper": "1-s2.0-S0378517323011092-main.pdf",
  "resolutions": [
    {"entity_id": "e1", "label": "Drug", "canonical_name": "adalimumab", "action": "MATCHED", "step": "exact", "matched_node": "adalimumab"},
    {"entity_id": "e2", "label": "Model", "canonical_name": "adalimumab_popPK_two_compartment", "action": "CREATED", "step": "new"}
  ],
  "summary": {"matched": 3, "created": 2, "flagged": 0}
}
```

### Output Summary

Print a summary table after processing:

```
## Paper Processed: "Population PK of Adalimumab in RA Patients"
DOI: 10.1234/example

| Action  | Label    | Count |
|---------|----------|-------|
| Created | Drug     | 1     |
| Merged  | Type     | 1     |
| Created | Model    | 1     |
| Created | Organism | 1     |

Relationships created: 4
Dedup candidates flagged: 0
```

---

## Mode B: Run Dedup Pass

**Trigger:** User says "run dedup", "deduplicate", or "check for duplicates".

Follow the full guide in `dedup-pass.md`. Summary:

1. **Phase 1** — Query candidate pairs (flagged nodes, same-label similar names, fulltext cross-match)
2. **Phase 2** — Compare properties and decide: SAME / DIFFERENT / UNCERTAIN
3. **Phase 3** — Execute merge Cypher (transfer relationships, merge aliases, delete duplicate). Use write patterns from the neo4j-cypher skill's `references/write-patterns.md`.

---

## Mode C: Schema Help

**Trigger:** User asks how to write or modify an extraction schema.

Refer to `schema-format.md` and help them author or extend their schema. Validate that:
- Every node type has a `canonical_name` property marked `[canonical]`
- Every relationship references valid node labels from the schema
- Normalization rules exist for enum-like properties

---

## Schema Initialization (First Run)

Before processing the first paper, ensure Neo4j has the required constraints and indexes. Generate these from the schema's Constraints section using the neo4j-cypher skill's write patterns, then execute via `mcp__neo4j__query`.

Verify with:
```cypher
SHOW CONSTRAINTS;
SHOW INDEXES;
```

---

## Error Handling

| Error | Recovery |
|-------|----------|
| PDF extraction fails on some pages | Report which pages failed, continue with extracted pages |
| Entity resolution query fails | Skip fuzzy step, fall back to exact + case-insensitive only |
| MERGE statement fails | Log the failed Cypher and entity, continue with remaining |
| MCP tool unavailable | Tell user to check neo4j MCP server is running |

---

## Reference Files

| File | Purpose |
|------|---------|
| `entity-resolution.md` | 5-step entity resolution algorithm |
| `dedup-pass.md` | Batch deduplication guide |
| `schema-format.md` | How to write extraction schemas |
| neo4j-cypher skill `references/write-patterns.md` | Cypher WRITE patterns |
| neo4j-cypher skill `SKILL.md` | Cypher READ patterns + modern syntax |
| pdf skill `SKILL.md` | PDF extraction patterns |
