# kg-qa — Natural Language Question Answering over the Knowledge Graph

## Trigger
- "find models for [drug]"
- "what drugs treat [disease]?"
- "show me all [entity type]"
- "which papers describe [entity]?"
- "compare models for [drug]"
- Any natural language question about the pharmacokinetic knowledge graph

## Purpose
Translate natural language questions into Cypher queries, execute them against Neo4j, and return formatted answers with source paper provenance.

## Flow

### Step 1: Understand the Schema
Read `schema/pk_schema.md` to understand the graph structure:
- **Nodes**: Model, Type, Drug, Organism, Disease — all keyed on `canonical_name`
- **Relationships**: IS_TYPE, CHARACTERIZES, STUDIED_IN, TREATS — all carry `source_papers` provenance
- **Indexes**: fulltext indexes on all node types for fuzzy search

### Step 2: Generate Cypher
Reference the `neo4j-cypher` skill patterns (modern syntax only). Key rules:
- Anchor queries on indexed `canonical_name` properties
- Use `CALL db.index.fulltext.queryNodes()` for fuzzy name matching
- Never use deprecated syntax (see neo4j-cypher skill references)
- Use CALL subqueries for complex aggregations

### Step 3: Execute Query
Run via cypher-shell:
```bash
cypher-shell -u neo4j -p docling-graph --format plain "YOUR CYPHER HERE"
```

### Step 4: Format Results
Present results as:
1. **Direct answer** to the user's question in natural language
2. **Table** of results when multiple rows returned
3. **Source papers** (DOIs) for every fact shown — query `source_papers` on both nodes and relationships

## Provenance-Aware Queries

CRITICAL: Always include provenance. Every fact in the graph has `source_papers` tracking which paper(s) provided the evidence.

### Example: Drug Lookup
```cypher
MATCH (m:Model)-[:CHARACTERIZES]->(d:Drug {canonical_name: "alirocumab"})
MATCH (m)-[r_type:IS_TYPE]->(t:Type)
RETURN m.canonical_name AS model,
       t.canonical_name AS type,
       m.source_papers AS model_sources,
       r_type.source_papers AS rel_sources
```

### Example: Models for a Drug (with provenance)
```cypher
MATCH (m:Model)-[r:CHARACTERIZES]->(d:Drug {canonical_name: "alirocumab"})
MATCH (m)-[:IS_TYPE]->(t:Type)
OPTIONAL MATCH (m)-[:STUDIED_IN]->(o:Organism)
OPTIONAL MATCH (m)-[:TREATS]->(dis:Disease)
RETURN m.canonical_name AS model,
       t.canonical_name AS type,
       collect(DISTINCT o.canonical_name) AS organisms,
       collect(DISTINCT dis.canonical_name) AS diseases,
       m.source_papers AS sources
```

### Example: Cross-Entity Traversal
```cypher
MATCH (d:Drug)<-[:CHARACTERIZES]-(m:Model)-[:TREATS]->(dis:Disease {canonical_name: "hypercholesterolemia"})
RETURN d.canonical_name AS drug,
       m.canonical_name AS model,
       m.source_papers AS sources
```

### Example: Fuzzy Name Search
```cypher
CALL db.index.fulltext.queryNodes("drug_fulltext", "aliro*")
YIELD node, score
WHERE score > 0.5
RETURN node.canonical_name AS drug, score
ORDER BY score DESC
```

### Example: Aggregation — Papers per Drug
```cypher
MATCH (d:Drug)
RETURN d.canonical_name AS drug,
       size(d.source_papers) AS paper_count,
       d.source_papers AS papers
ORDER BY paper_count DESC
```

### Example: Edge Provenance
```cypher
MATCH (m:Model)-[r:CHARACTERIZES]->(d:Drug)
RETURN m.canonical_name AS model,
       d.canonical_name AS drug,
       r.source_papers AS asserted_by,
       r.extraction_source AS loaded_from
```

### Example: Multi-Paper Entities
```cypher
MATCH (n)
WHERE size(n.source_papers) > 1
RETURN labels(n)[0] AS label,
       n.canonical_name AS name,
       n.source_papers AS papers
```

## Query Patterns by Question Type

| User asks about... | Query pattern |
|---|---|
| Drug lookup | `MATCH (d:Drug {canonical_name: $name})` |
| Models for a drug | `(m:Model)-[:CHARACTERIZES]->(d:Drug)` |
| Model type | `(m:Model)-[:IS_TYPE]->(t:Type)` |
| Diseases treated | `(m:Model)-[:TREATS]->(dis:Disease)` |
| Organisms studied | `(m:Model)-[:STUDIED_IN]->(o:Organism)` |
| Model comparison | Multiple model matches + parameter comparison |
| Cross-entity | Chain relationships through Model hub node |
| "Which papers..." | Return `source_papers` from nodes or relationships |
| Fuzzy/partial name | Use fulltext index with wildcards |

## Response Format

Always structure responses as:

1. **Answer**: Plain language answer to the question
2. **Details**: Table or list of results
3. **Sources**: List of DOIs that support the answer

Example response:
> There are 2 PK models for alirocumab in the knowledge graph:
>
> | Model | Type | Diseases |
> |---|---|---|
> | alirocumab_popPK_two_compartment_tmdd_qss | two_compartment_tmdd_qss | hypercholesterolemia, familial_hypercholesterolemia |
> | alirocumab_popPK_two_compartment_michaelis_menten | two_compartment_michaelis_menten | hypercholesterolemia |
>
> Sources:
> - 10.1007/s40262-016-0505-1
> - 10.1007/s40262-018-0669-y
