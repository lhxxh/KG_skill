---
name: kg-qa
description: Answer natural language questions about the knowledge graph. Translates questions into Cypher queries, executes against Neo4j, and returns formatted answers with source paper provenance. Triggered by questions like "find [entity]", "what [entities] are related to [entity]?", "show me all [entity type]", "which papers describe [entity]?", "compare [entities]", or any natural language question about the knowledge graph.
---

# Knowledge Graph Question Answering Guide

## Overview

This guide covers translating natural language questions into Cypher queries, executing them against the Neo4j knowledge graph, and formatting provenance-aware answers. The graph schema is defined in the schema file (e.g., `schema/pk_schema.md`) — read it first to understand available node types, relationships, and properties. For Cypher syntax and modern patterns, see the neo4j-cypher skill. For data ingestion, see the kg-pipeline skill.

## Quick Start

```bash
# Read the schema to understand the graph structure
cat schema/pk_schema.md

# Execute a Cypher query
cypher-shell -u neo4j -p docling-graph --format plain \
  "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total ORDER BY total DESC;"
```

## Graph Schema

Read the schema file to understand:
- **Node types**: defined as `**Label** (description)` with properties
- **Relationships**: defined as `(Source)-[:TYPE]->(Target)` with cardinality
- **Constraints**: uniqueness on `canonical_name` per label
- **Fulltext indexes**: for fuzzy search

All nodes are keyed on `canonical_name` (unique, indexed). All relationships carry provenance: `source_papers` (string[]) and `extraction_source` (string).

## Query Execution

```bash
cypher-shell -u neo4j -p docling-graph --format plain "YOUR CYPHER HERE"
```

## Cypher Query Patterns

### Entity Lookup

```cypher
MATCH (n:Label {canonical_name: "entity_name"})
RETURN n.canonical_name AS name, n.source_papers AS sources
```

### Traverse a Relationship

```cypher
MATCH (a:SourceLabel)-[r:REL_TYPE]->(b:TargetLabel {canonical_name: "target_name"})
RETURN a.canonical_name AS source,
       b.canonical_name AS target,
       r.source_papers AS rel_sources
```

### Entity with Full Context

```cypher
MATCH (a:Label {canonical_name: "name"})-[r]->(b)
RETURN a.canonical_name AS entity,
       type(r) AS relationship,
       labels(b)[0] AS related_type,
       b.canonical_name AS related_name,
       r.source_papers AS sources
```

### Reverse Traversal

```cypher
MATCH (a:Label)<-[r]-(b)
WHERE a.canonical_name = "name"
RETURN b.canonical_name AS related,
       labels(b)[0] AS type,
       type(r) AS relationship,
       r.source_papers AS sources
```

### Fuzzy Name Search

```cypher
CALL db.index.fulltext.queryNodes("label_fulltext", "partial*")
YIELD node, score
WHERE score > 0.5
RETURN node.canonical_name AS name, labels(node)[0] AS label, score
ORDER BY score DESC
```

### Aggregation — Papers per Entity

```cypher
MATCH (n:Label)
RETURN n.canonical_name AS name,
       size(n.source_papers) AS paper_count,
       n.source_papers AS papers
ORDER BY paper_count DESC
```

### Edge Provenance

```cypher
MATCH (a)-[r]->(b)
RETURN a.canonical_name AS source,
       type(r) AS relationship,
       b.canonical_name AS target,
       r.source_papers AS asserted_by,
       r.extraction_source AS loaded_from
LIMIT 20
```

### Multi-Paper Entities

```cypher
MATCH (n)
WHERE size(n.source_papers) > 1
RETURN labels(n)[0] AS label,
       n.canonical_name AS name,
       n.source_papers AS papers
```

### Cross-Entity Traversal

Chain relationships through a hub node to find indirect connections:

```cypher
MATCH (a:LabelA)<-[:REL1]-(hub:HubLabel)-[:REL2]->(b:LabelB {canonical_name: "name"})
RETURN a.canonical_name AS result,
       hub.canonical_name AS via,
       hub.source_papers AS sources
```

### All Nodes of a Type

```cypher
MATCH (n:Label)
RETURN n.canonical_name AS name, n.source_papers AS sources
ORDER BY n.canonical_name
```

## Response Format

Always structure responses with provenance:

1. **Answer**: Plain language answer to the question
2. **Details**: Table or list of results
3. **Sources**: DOIs that support the answer

Example:

> There are 2 matching entities in the knowledge graph:
>
> | Name | Type | Related |
> |---|---|---|
> | entity_a | type_x | related_1, related_2 |
> | entity_b | type_y | related_3 |
>
> Sources:
> - 10.xxxx/paper-1
> - 10.xxxx/paper-2

## Quick Reference

| User asks about... | Query pattern |
|---|---|
| Entity lookup | `MATCH (n:Label {canonical_name: $name})` |
| Related entities | Traverse relationship from/to the entity |
| All of a type | `MATCH (n:Label) RETURN n.canonical_name` |
| Entity comparison | Multiple matches + property comparison |
| Cross-entity | Chain relationships through hub node |
| "Which papers..." | Return `source_papers` from nodes or relationships |
| Fuzzy/partial name | Use fulltext index with wildcards |
| Graph summary | `RETURN labels(n)[0], count(n)` grouped |
