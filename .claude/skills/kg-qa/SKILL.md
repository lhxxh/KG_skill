---
name: kg-qa
description: Answer natural language questions about the knowledge graph. Translates questions into Cypher queries, executes against Neo4j, and returns formatted answers with source paper provenance. Triggered by questions like "find [entity]", "what [entities] are related to [entity]?", "show me all [entity type]", "which papers describe [entity]?", "compare [entities]", or any natural language question about the knowledge graph.
---

# Knowledge Graph Question Answering Guide

## Overview

This guide covers answering natural language questions against a Neo4j knowledge graph in three stages: (1) discover the relevant schema, (2) translate the question into Cypher, (3) execute against Neo4j and format the answer. For Cypher syntax and modern patterns, see the neo4j-cypher skill. For data ingestion, see the kg-pipeline skill.

## Three-Stage Pipeline

### Stage 1: Discover the Schema

Browse the project to find schema files that describe the graph structure. Schema files may live in a `schema/` folder or elsewhere.

```bash
ls schema/
```

Read the schema file to understand what node types (labels) and relationships exist in the graph. The schema tells you:
- What labels are available and what properties each node type has
- What relationships connect which node types
- What constraints and indexes are defined (e.g., uniqueness keys, fulltext indexes)

If multiple schema files exist, pick the one whose node types and relationships best match the entities in the user's question.

### Stage 2: Translate Question to Cypher

Using the schema from Stage 1, generate a valid Cypher query. Reference the neo4j-cypher skill for modern syntax patterns. Key rules:
- Anchor queries on the unique key property identified in the schema (often constrained/indexed)
- Use fulltext indexes (if defined in the schema) for fuzzy name matching
- Never use deprecated syntax (see neo4j-cypher skill references)
- Include provenance properties in the RETURN clause if the schema tracks them
- See the neo4j-cypher skill for query patterns (read, write, fulltext, traversal, aggregation)

### Stage 3: Execute Against Neo4j

Read Neo4j connection settings from `.claude/settings.json` under `mcpServers.neo4j.env` to get the URI, username, and password. Execute the Cypher query via `cypher-shell`:

```bash
cypher-shell \
  -a $NEO4J_URI \
  -u $NEO4J_USERNAME \
  -p $NEO4J_PASSWORD \
  --format plain \
  "YOUR CYPHER HERE"
```

Format the results as a clear answer with supporting evidence from the graph.

## Response Format

Structure responses as:

1. **Answer**: Plain language answer to the question
2. **Details**: Table or list of results
3. **Sources**: Supporting evidence (e.g., provenance properties if the schema tracks them)

## Quick Reference

| Stage | What to do |
|---|---|
| 1. Discover schema | Browse project for schema files, read the one matching the user's question |
| 2. Translate to Cypher | Map question to schema's node types + relationships; see neo4j-cypher skill for patterns |
| 3. Execute & format | Read creds from `.claude/settings.json`, run via `cypher-shell`, format answer |
