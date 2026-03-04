# Batch Deduplication Pass

A separate invocation from paper processing. Run after processing a batch of papers or when `dedup_candidate` nodes accumulate.

All Cypher in this guide follows patterns from the neo4j-cypher skill. Execute queries via `mcp__neo4j__query`.

## When to Run

- After processing 10+ papers
- When the user says "run dedup" or "deduplicate"
- When flagged candidates accumulate

## Three Phases

### Phase 1 — Find Candidate Pairs

#### 1a. Flagged Candidates

```cypher
MATCH (n)
WHERE n.dedup_candidate = true
RETURN labels(n)[0] AS label, n.canonical_name, n.dedup_reason,
       elementId(n) AS eid
ORDER BY labels(n)[0], n.canonical_name
```

#### 1b. Same-Label Similar Names

```cypher
MATCH (a:Drug), (b:Drug)
WHERE elementId(a) < elementId(b)
  AND (
    a.canonical_name CONTAINS b.canonical_name
    OR b.canonical_name CONTAINS a.canonical_name
    OR a.canonical_name STARTS WITH left(b.canonical_name, 5)
  )
RETURN a.canonical_name AS name_a, b.canonical_name AS name_b,
       a.source_papers AS papers_a, b.source_papers AS papers_b,
       elementId(a) AS eid_a, elementId(b) AS eid_b
```

Repeat for each label (Model, Type, Organism, Disease).

#### 1c. Fulltext Cross-Match

```cypher
MATCH (a:Drug)
CALL db.index.fulltext.queryNodes("drug_fulltext", a.canonical_name)
YIELD node AS b, score
WHERE elementId(a) <> elementId(b)
  AND score > 0.7
  AND elementId(a) < elementId(b)
RETURN a.canonical_name AS name_a, b.canonical_name AS name_b,
       score,
       elementId(a) AS eid_a, elementId(b) AS eid_b
ORDER BY score DESC
```

Repeat for each label/index pair.

---

### Phase 2 — Compare and Decide

For each candidate pair, decide:

| Decision | Criteria | Action |
|----------|----------|--------|
| **SAME** | Same real-world entity, properties compatible | Merge nodes |
| **DIFFERENT** | Different entities with similar names | Clear `dedup_candidate` flag |
| **UNCERTAIN** | Cannot determine without domain expertise | Keep flag, report to user |

#### Comparison Queries

Full properties:
```cypher
MATCH (n)
WHERE elementId(n) IN [$eid_a, $eid_b]
RETURN elementId(n) AS eid, labels(n)[0] AS label, properties(n) AS props
```

Relationships:
```cypher
MATCH (n)-[r]->(m)
WHERE elementId(n) IN [$eid_a, $eid_b]
RETURN elementId(n) AS source_eid, type(r) AS rel_type,
       labels(m)[0] AS target_label, m.canonical_name AS target_name
```

#### Comparison Checklist

1. **Names** — spelling variants, abbreviations, or genuinely different?
2. **Properties** — do non-name properties agree? (same `drug_type`, `organism`, etc.)
3. **source_papers** — from papers discussing the same topic?
4. **Relationships** — connect to the same or compatible nodes?

---

### Phase 3 — Execute Merge

For SAME decisions, merge the duplicate into the primary node.

#### Choosing the Primary

1. More `source_papers` (more evidence)
2. If tied → earlier `created_at`
3. If tied → more relationships

#### Merge Execution

Use the node merge patterns from the neo4j-cypher skill's `references/write-patterns.md`:

**Non-APOC approach** (5 steps):
1. Merge aliases into primary
2. Merge `source_papers` into primary
3. Transfer incoming relationships (per rel type)
4. Transfer outgoing relationships (per rel type)
5. `DETACH DELETE` the duplicate

**APOC approach** (if available):
```cypher
MATCH (primary:Drug {canonical_name: $primary_name})
MATCH (dup:Drug {canonical_name: $dup_name})
CALL apoc.refactor.mergeNodes([primary, dup], {properties: "combine", mergeRels: true})
YIELD node
RETURN node
```

Check APOC availability: `RETURN apoc.version()` — if it errors, use non-APOC.

#### Clear Resolved Flags

For DIFFERENT decisions:
```cypher
MATCH (n)
WHERE elementId(n) IN [$eid_a, $eid_b]
REMOVE n.dedup_candidate, n.dedup_reason
```

---

## Output Summary

```
## Dedup Pass Results

Candidate pairs examined: 12
Decisions:
  - SAME (merged): 4
  - DIFFERENT (cleared): 6
  - UNCERTAIN (kept flagged): 2

Merged pairs:
  1. Drug: "adalimumab" ← "adalimumab-atto" (alias variant)
  2. Disease: "rheumatoid_arthritis" ← "ra" (abbreviation)
  3. Type: "two_compartment" ← "2-compartment" (naming variant)
  4. Organism: "human" ← "homo_sapiens" (scientific name)

Uncertain pairs (need manual review):
  1. Drug: "etanercept" vs "etanercept_biosimilar" — different formulations?
  2. Model: "warfarin_pk_1cmt" vs "warfarin_popPK_1cmt" — same model?
```
