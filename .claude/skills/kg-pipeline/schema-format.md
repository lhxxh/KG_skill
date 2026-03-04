# Schema Authoring Guide

Defines the markdown format for extraction schemas used by the KG pipeline. The schema tells the pipeline what entities and relationships to extract from papers.

Schema files live in `schema/`. Default: `schema/pk_schema.md`.

## Format Overview

A schema has three sections:
1. **Node Types** — what entities to extract
2. **Relationships** — how entities connect
3. **Constraints** — Neo4j index declarations

---

## Node Type Blocks

```markdown
**Label** (human-readable description)
- `canonical_name`: string [canonical] — description → **normalization rule** (e.g., `"example"`)
- `property_name`: type — description (e.g., `"example_value"`)
```

### Required

Every node type MUST have:
1. A `canonical_name` property marked `[canonical]` — the MERGE key for deduplication
2. At least one additional descriptive property

### Property Types

| Type | Neo4j Storage | Example |
|------|--------------|---------|
| `string` | String | `"adalimumab"` |
| `string[]` | List of strings | `["eq1", "eq2"]` |
| `object` | JSON string (serialized) | `'{"CL": 0.5}'` |
| `number` | Float or Integer | `42`, `3.14` |

### Normalization Rules

Declare inline with `→` notation:

```markdown
- `drug_name`: string — drug name → **normalize to INN/generic, lowercase**
- `model_type`: string — model structure → **normalize to snake_case**
- `organism`: string — species → **normalize to lowercase common name**
```

### Example Node Type

```markdown
**Drug** (drug identity and class)
- `canonical_name`: string [canonical] — lowercase INN drug name → **normalize to INN/generic, lowercase** (e.g., `"adalimumab"`)
- `drug_name`: string — same as canonical_name (e.g., `"adalimumab"`)
- `drug_type`: string — drug modality → **normalize to snake_case** (e.g., `"small_molecule"`, `"monoclonal_antibody"`)
- `aliases`: string[] — alternative names, brand names (e.g., `["humira"]`)
```

---

## Relationship Blocks

```markdown
### Relationships

- `(Model)-[:IS_TYPE]->(Type)` — links a PK model to its structural category
- `(Model)-[:CHARACTERIZES]->(Drug)` — the model describes the PK of this drug
```

### With Properties

```markdown
- `(Model)-[:CHARACTERIZES]->(Drug)` — the model describes the PK of this drug
  - `confidence`: string — extraction confidence (`"high"`, `"medium"`, `"low"`)
```

### Cardinality Hints

```markdown
- `(Model)-[:IS_TYPE]->(Type)` — **one-to-one**: each model has exactly one type
- `(Model)-[:CHARACTERIZES]->(Drug)` — **many-to-one**: multiple models can characterize the same drug
```

---

## Constraints Section

```markdown
### Constraints

- `Drug.canonical_name` — UNIQUE
- `Model.canonical_name` — UNIQUE
```

### Fulltext Indexes

```markdown
### Fulltext Indexes

- `drug_fulltext` on Drug: `[canonical_name, drug_name]`
- `model_fulltext` on Model: `[canonical_name]`
```

These are translated into Cypher using the neo4j-cypher skill's write patterns.

---

## Canonical Name Construction

| Label | Rule | Example |
|-------|------|---------|
| Drug | INN/generic name, lowercase | `"adalimumab"` |
| Type | Model type, snake_case | `"two_compartment"` |
| Organism | Common name, lowercase | `"human"` |
| Disease | Disease name, snake_case | `"rheumatoid_arthritis"` |
| Model | `{drug}_{descriptor}_{type}` | `"adalimumab_popPK_two_compartment"` |

---

## Validation Checklist

- [ ] Every node type has exactly one `[canonical]` property
- [ ] Every relationship references labels defined in the schema
- [ ] Normalization rules exist for enum-like string properties
- [ ] No nested objects — use JSON strings for maps
- [ ] Examples provided for each property
- [ ] Constraints section lists all canonical names as UNIQUE

---

## Extending the Schema

**Add a new node type:**
1. Add the node type block with properties
2. Add relationships to existing labels
3. Add UNIQUE constraint for `canonical_name`
4. Add fulltext index if fuzzy matching needed

**Add a property to an existing type:**
1. Add the property line with type, description, example
2. Add normalization rules if enum-like
3. Existing nodes won't have this property until updated
