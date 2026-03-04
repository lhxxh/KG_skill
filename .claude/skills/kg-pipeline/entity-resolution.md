# Entity Resolution — 5-Step Lookup Algorithm

Before inserting any entity into Neo4j, check whether it already exists. This prevents duplicates and ensures multiple papers contributing facts about the same entity point to a single node.

Run these steps in order for each extracted entity. Stop at the first match.

| Step | Method | When It Matches |
|------|--------|-----------------|
| 1 | Exact match on `canonical_name` | Same paper reprocessed, or identical naming |
| 2 | Case-insensitive match | "Adalimumab" vs "adalimumab" |
| 3 | Alias lookup | Brand name vs generic name |
| 4 | Fulltext fuzzy search | Spelling variations, abbreviations |
| 5 | Create new | Genuinely new entity |

All queries below are executed via `mcp__neo4j__query`. Refer to the neo4j-cypher skill for syntax guidance.

---

## Step 1 — Exact Match

```cypher
MATCH (n:Drug {canonical_name: $canonical_name})
RETURN n, elementId(n) AS eid
```

Replace `Drug` with the appropriate label. If a row returns → use this node. **Done.**

---

## Step 2 — Case-Insensitive Match

```cypher
MATCH (n:Drug)
WHERE toLower(n.canonical_name) = toLower($canonical_name)
RETURN n, elementId(n) AS eid
```

If matched, also add the extracted name as an alias if it differs:

```cypher
MATCH (n:Drug)
WHERE toLower(n.canonical_name) = toLower($canonical_name)
  AND n.canonical_name <> $canonical_name
  AND NOT $canonical_name IN coalesce(n.aliases, [])
SET n.aliases = coalesce(n.aliases, []) + $canonical_name
```

**Done** — use the matched node.

---

## Step 3 — Alias Lookup

```cypher
MATCH (n:Drug)
WHERE $canonical_name IN n.aliases
   OR toLower($canonical_name) IN [a IN coalesce(n.aliases, []) | toLower(a)]
RETURN n, elementId(n) AS eid
```

Common alias scenarios:
- Brand name → generic: "Humira" → `canonical_name: "adalimumab"`
- Abbreviation → full name: "CsA" → `canonical_name: "cyclosporine"`
- Alternative spelling: "paracetamol" → `canonical_name: "acetaminophen"`

If matched → **done.**

---

## Step 4 — Fulltext Fuzzy Search

Requires fulltext indexes (see neo4j-cypher skill `references/write-patterns.md`).

```cypher
CALL db.index.fulltext.queryNodes("drug_fulltext", $canonical_name)
YIELD node, score
WHERE score > 0.7
RETURN node, score, node.canonical_name AS matched_name, elementId(node) AS eid
ORDER BY score DESC
LIMIT 3
```

### Fulltext Index Names

| Label | Index Name |
|-------|-----------|
| Drug | `drug_fulltext` |
| Model | `model_fulltext` |
| Type | `type_fulltext` |
| Organism | `organism_fulltext` |
| Disease | `disease_fulltext` |

### Decision Matrix

| Score | Property Agreement | Action |
|-------|-------------------|--------|
| > 0.9 | Most properties match | **MERGE** — same entity |
| 0.8–0.9 | Some properties match | **FLAG** — merge but set `dedup_candidate: true` |
| 0.7–0.8 | Few properties match | **FLAG** — create new, flag both nodes |
| < 0.7 | N/A | **SKIP** — proceed to Step 5 |

To check property agreement, query the candidate's full properties:

```cypher
CALL db.index.fulltext.queryNodes("drug_fulltext", $canonical_name)
YIELD node, score
WHERE score > 0.7
RETURN node.canonical_name, node.drug_name, node.drug_type, score
```

Compare in context: do `drug_type`, `drug_name`, or other properties agree with the extracted entity?

### Flagging

Use the dedup flagging pattern from neo4j-cypher `references/write-patterns.md`:

```cypher
MATCH (n:Drug {canonical_name: $name})
SET n.dedup_candidate = true,
    n.dedup_reason = "fuzzy match with '" + $other_name + "' (score: " + toString($score) + ")"
```

---

## Step 5 — Create New

No match at any step. Create the entity via MERGE using write patterns from the neo4j-cypher skill.

---

## Resolution Map

Track all results before generating Cypher:

```
Resolution Map:
  e1 (Drug: adalimumab)                          → MATCHED step 1 (exact)
  e2 (Model: adalimumab_popPK_two_compartment)    → CREATE NEW (step 5)
  e3 (Type: two_compartment)                      → MATCHED step 2 (case-insensitive)
  e4 (Organism: human)                            → MATCHED step 1 (exact)
  e5 (Disease: rheumatoid_arthritis)              → MATCHED step 3 (alias: "RA")
```

Use the map to:
1. Use the existing node's `canonical_name` for matched entities in MERGE statements
2. Generate relationship MERGEs using resolved canonical names
3. Count created vs merged for the summary

---

## Multiple Matches

If Step 4 returns multiple candidates above threshold:

1. Pick the highest-scoring candidate
2. If two are within 0.05 of each other, flag both as `dedup_candidate` and pick the one with more `source_papers`
3. If still tied, pick the one with earlier `created_at`

---

## Performance

- Steps 1–3 use indexed lookups — fast
- Step 4 uses fulltext indexes — moderate cost
- For ~10 entities per paper, expect 10–40 total queries
- Run resolution for all entities before generating any MERGE statements
