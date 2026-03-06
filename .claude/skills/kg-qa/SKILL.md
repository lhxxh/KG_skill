---
name: kg-qa
description: Answer natural language questions about the pharmacokinetic knowledge graph. Translates questions into Cypher queries, executes against Neo4j, and returns formatted answers with source paper provenance. Triggered by questions like "find models for [drug]", "what drugs treat [disease]?", "show me all [entity type]", "which papers describe [entity]?", "compare models for [drug]", or any natural language question about the PK knowledge graph.
---

# PK Knowledge Graph Question Answering Guide

## Overview

This guide covers translating natural language questions into Cypher queries, executing them against the Neo4j PK knowledge graph, and formatting provenance-aware answers. For Cypher syntax and modern patterns, see the neo4j-cypher skill. For data ingestion, see the kg-pipeline skill. The graph schema is defined in `schema/pk_schema.md`.

## Quick Start

```bash
# Execute a Cypher query
cypher-shell -u neo4j -p docling-graph --format plain \
  "MATCH (m:Model)-[:CHARACTERIZES]->(d:Drug {canonical_name: 'alirocumab'})
   RETURN m.canonical_name AS model, m.source_papers AS sources;"
```

## Graph Schema Reference

### Nodes

All nodes are keyed on `canonical_name` (unique, indexed).

| Label | Key Properties | Fulltext Index |
|-------|---------------|----------------|
| Model | `canonical_name`, `mathematical_equations`, `parameter_means`, `parameter_iiv_std_dev` | `model_fulltext` |
| Drug | `canonical_name`, `drug_name`, `drug_type`, `aliases` | `drug_fulltext` |
| Type | `canonical_name`, `model_type` | `type_fulltext` |
| Organism | `canonical_name`, `organism` | `organism_fulltext` |
| Disease | `canonical_name`, `name`, `aliases` | `disease_fulltext` |

### Relationships

All relationships carry provenance: `source_papers` (string[]) and `extraction_source` (string).

| Type | Pattern | Cardinality |
|------|---------|-------------|
| IS_TYPE | `(Model)-[:IS_TYPE]->(Type)` | one-to-one |
| CHARACTERIZES | `(Model)-[:CHARACTERIZES]->(Drug)` | many-to-one |
| STUDIED_IN | `(Model)-[:STUDIED_IN]->(Organism)` | many-to-many |
| TREATS | `(Model)-[:TREATS]->(Disease)` | many-to-many |

## Query Execution

```bash
cypher-shell -u neo4j -p docling-graph --format plain "YOUR CYPHER HERE"
```

## Cypher Query Patterns

### Drug Lookup

```cypher
MATCH (d:Drug {canonical_name: "alirocumab"})
RETURN d.canonical_name AS drug,
       d.drug_type AS type,
       d.aliases AS aliases,
       d.source_papers AS sources
```

### Models for a Drug

```cypher
MATCH (m:Model)-[:CHARACTERIZES]->(d:Drug {canonical_name: "alirocumab"})
MATCH (m)-[r_type:IS_TYPE]->(t:Type)
RETURN m.canonical_name AS model,
       t.canonical_name AS type,
       m.source_papers AS model_sources,
       r_type.source_papers AS rel_sources
```

### Models with Full Context

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

### Drugs that Treat a Disease

```cypher
MATCH (d:Drug)<-[:CHARACTERIZES]-(m:Model)-[:TREATS]->(dis:Disease {canonical_name: "hypercholesterolemia"})
RETURN DISTINCT d.canonical_name AS drug,
       d.drug_type AS type,
       d.source_papers AS sources
```

### Cross-Entity Traversal

```cypher
MATCH (d:Drug)<-[:CHARACTERIZES]-(m:Model)-[:TREATS]->(dis:Disease {canonical_name: "hypercholesterolemia"})
RETURN d.canonical_name AS drug,
       m.canonical_name AS model,
       m.source_papers AS sources
```

### Fuzzy Name Search

```cypher
CALL db.index.fulltext.queryNodes("drug_fulltext", "aliro*")
YIELD node, score
WHERE score > 0.5
RETURN node.canonical_name AS drug, score
ORDER BY score DESC
```

### Aggregation — Papers per Drug

```cypher
MATCH (d:Drug)
RETURN d.canonical_name AS drug,
       size(d.source_papers) AS paper_count,
       d.source_papers AS papers
ORDER BY paper_count DESC
```

### Edge Provenance

```cypher
MATCH (m:Model)-[r:CHARACTERIZES]->(d:Drug)
RETURN m.canonical_name AS model,
       d.canonical_name AS drug,
       r.source_papers AS asserted_by,
       r.extraction_source AS loaded_from
```

### Multi-Paper Entities

```cypher
MATCH (n)
WHERE size(n.source_papers) > 1
RETURN labels(n)[0] AS label,
       n.canonical_name AS name,
       n.source_papers AS papers
```

### Model Parameters

```cypher
MATCH (m:Model {canonical_name: "alirocumab_popPK_two_compartment_tmdd_qss"})
RETURN m.canonical_name AS model,
       m.mathematical_equations AS equations,
       m.parameter_means AS params,
       m.parameter_iiv_std_dev AS iiv
```

## Response Format

Always structure responses with provenance:

1. **Answer**: Plain language answer to the question
2. **Details**: Table or list of results
3. **Sources**: DOIs that support the answer

Example:

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

## Quick Reference

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
| Model parameters | Return `parameter_means`, `parameter_iiv_std_dev` |
