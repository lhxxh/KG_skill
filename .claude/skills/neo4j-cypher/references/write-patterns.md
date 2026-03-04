# Neo4j Cypher Write Patterns

This reference extends the neo4j-cypher skill with WRITE operations: MERGE, SET, constraints, fulltext indexes, and provenance tracking. Used by the kg-pipeline skill for graph construction.

## Schema Initialization

### Uniqueness Constraints

Create UNIQUE constraints on `canonical_name` for each node type. This is required before any MERGE operations.

```cypher
CREATE CONSTRAINT drug_canonical IF NOT EXISTS
FOR (n:Drug) REQUIRE n.canonical_name IS UNIQUE;

CREATE CONSTRAINT model_canonical IF NOT EXISTS
FOR (n:Model) REQUIRE n.canonical_name IS UNIQUE;

CREATE CONSTRAINT type_canonical IF NOT EXISTS
FOR (n:Type) REQUIRE n.canonical_name IS UNIQUE;

CREATE CONSTRAINT organism_canonical IF NOT EXISTS
FOR (n:Organism) REQUIRE n.canonical_name IS UNIQUE;

CREATE CONSTRAINT disease_canonical IF NOT EXISTS
FOR (n:Disease) REQUIRE n.canonical_name IS UNIQUE;
```

### Fulltext Indexes

For fuzzy entity resolution:

```cypher
CREATE FULLTEXT INDEX drug_fulltext IF NOT EXISTS
FOR (n:Drug) ON EACH [n.canonical_name, n.drug_name];

CREATE FULLTEXT INDEX model_fulltext IF NOT EXISTS
FOR (n:Model) ON EACH [n.canonical_name];

CREATE FULLTEXT INDEX type_fulltext IF NOT EXISTS
FOR (n:Type) ON EACH [n.canonical_name, n.model_type];

CREATE FULLTEXT INDEX organism_fulltext IF NOT EXISTS
FOR (n:Organism) ON EACH [n.canonical_name, n.organism];

CREATE FULLTEXT INDEX disease_fulltext IF NOT EXISTS
FOR (n:Disease) ON EACH [n.canonical_name, n.name];
```

### Verification

```cypher
SHOW CONSTRAINTS;
SHOW INDEXES;
```

---

## Node MERGE Pattern

MERGE on `canonical_name` — the unique key per node type. Use `ON CREATE SET` for initial properties and `ON MATCH SET` to append provenance only.

```cypher
MERGE (n:Drug {canonical_name: $canonical_name})
ON CREATE SET
  n.drug_name = $drug_name,
  n.drug_type = $drug_type,
  n.source_papers = [$doi],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT $doi IN n.source_papers
    THEN n.source_papers + $doi
    ELSE n.source_papers
  END
```

**Rules:**
- `ON CREATE SET` — all properties + `source_papers` array + `created_at`
- `ON MATCH SET` — only append to `source_papers`; never overwrite existing property values
- This makes reprocessing the same paper idempotent

---

## Map Properties as JSON Strings

Neo4j does not support map-type properties. Serialize objects as JSON strings:

```cypher
MERGE (n:Model {canonical_name: $canonical_name})
ON CREATE SET
  n.mathematical_equations = ["dA1/dt = -Ka*A1", "dA2/dt = Ka*A1 - (CL/V1)*A2"],
  n.parameter_means = '{"CL": 0.0135, "V1": 3.5, "Ka": 0.015}',
  n.parameter_iiv_std_dev = '{"CL_iiv_cv%": 42, "V1_iiv_cv%": 28}',
  n.source_papers = [$doi],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT $doi IN n.source_papers
    THEN n.source_papers + $doi
    ELSE n.source_papers
  END
```

- Array properties (e.g., `mathematical_equations`) → Neo4j native list
- Key-value maps (e.g., `parameter_means`) → JSON string

---

## Relationship MERGE Pattern

Use two MATCH clauses for endpoints, then MERGE the relationship. Do NOT MERGE nodes and relationship in a single statement.

```cypher
MATCH (source:Model {canonical_name: $source_name})
MATCH (target:Drug {canonical_name: $target_name})
MERGE (source)-[r:CHARACTERIZES]->(target)
ON CREATE SET
  r.source_papers = [$doi],
  r.created_at = datetime()
ON MATCH SET
  r.source_papers = CASE
    WHEN NOT $doi IN r.source_papers
    THEN r.source_papers + $doi
    ELSE r.source_papers
  END
```

---

## Alias Management

### Adding Aliases on Create

```cypher
MERGE (n:Drug {canonical_name: $canonical_name})
ON CREATE SET
  n.drug_name = $drug_name,
  n.aliases = $aliases,
  n.source_papers = [$doi],
  n.created_at = datetime()
ON MATCH SET
  n.source_papers = CASE
    WHEN NOT $doi IN n.source_papers
    THEN n.source_papers + $doi
    ELSE n.source_papers
  END
```

### Appending a New Alias

```cypher
MATCH (n:Drug {canonical_name: $canonical_name})
WHERE NOT $alias IN coalesce(n.aliases, [])
SET n.aliases = coalesce(n.aliases, []) + $alias
```

---

## Provenance (source_papers)

Every node and relationship tracks which papers assert the fact:

```cypher
// On create
ON CREATE SET n.source_papers = [$doi]

// On match — append if not present
ON MATCH SET n.source_papers = CASE
  WHEN NOT $doi IN n.source_papers
  THEN n.source_papers + $doi
  ELSE n.source_papers
END
```

### Query Provenance

```cypher
// All nodes from a paper
MATCH (n)
WHERE $doi IN n.source_papers
RETURN labels(n)[0] AS label, n.canonical_name, size(n.source_papers) AS paper_count

// Facts supported by multiple papers
MATCH (n)
WHERE size(n.source_papers) > 1
RETURN labels(n)[0] AS label, n.canonical_name, n.source_papers
```

---

## Dedup Candidate Flagging

```cypher
MATCH (n:Drug {canonical_name: $canonical_name})
SET n.dedup_candidate = true,
    n.dedup_reason = $reason
```

Query flagged candidates:

```cypher
MATCH (n)
WHERE n.dedup_candidate = true
RETURN labels(n)[0] AS label, n.canonical_name, n.dedup_reason
```

---

## Verification Queries

After inserting a paper's data:

```cypher
// Nodes by label for a paper
MATCH (n)
WHERE $doi IN n.source_papers
RETURN labels(n)[0] AS label, count(n) AS count

// Relationships for a paper
MATCH ()-[r]->()
WHERE $doi IN r.source_papers
RETURN type(r) AS rel_type, count(r) AS count

// Full graph summary
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS total
ORDER BY total DESC
```

---

## Node Merge for Dedup (Non-APOC)

Transfer relationships from a duplicate node to the primary:

```cypher
// 1. Merge aliases
MATCH (primary:Drug {canonical_name: $primary_name})
MATCH (dup:Drug {canonical_name: $dup_name})
SET primary.aliases = coalesce(primary.aliases, []) + $dup_name + coalesce(dup.aliases, [])

// 2. Merge source_papers
MATCH (primary:Drug {canonical_name: $primary_name})
MATCH (dup:Drug {canonical_name: $dup_name})
SET primary.source_papers = primary.source_papers + [p IN dup.source_papers WHERE NOT p IN primary.source_papers]

// 3. Transfer incoming relationships (repeat per rel type)
MATCH (dup:Drug {canonical_name: $dup_name})<-[r:CHARACTERIZES]-(source)
MATCH (primary:Drug {canonical_name: $primary_name})
MERGE (source)-[r2:CHARACTERIZES]->(primary)
ON CREATE SET r2 = properties(r)
DELETE r

// 4. Transfer outgoing relationships (repeat per rel type)
MATCH (dup:Drug {canonical_name: $dup_name})-[r:STUDIED_IN]->(target)
MATCH (primary:Drug {canonical_name: $primary_name})
MERGE (primary)-[r2:STUDIED_IN]->(target)
ON CREATE SET r2 = properties(r)
DELETE r

// 5. Delete duplicate
MATCH (dup:Drug {canonical_name: $dup_name})
DETACH DELETE dup
```

## Node Merge (With APOC)

```cypher
MATCH (primary:Drug {canonical_name: $primary_name})
MATCH (dup:Drug {canonical_name: $dup_name})
CALL apoc.refactor.mergeNodes([primary, dup], {properties: "combine", mergeRels: true})
YIELD node
RETURN node
```

## Execution Tips

- Execute one MERGE statement at a time via `mcp__neo4j__query`
- Always create constraints BEFORE any MERGE operations
- If a MERGE fails, log the error and continue with remaining statements
