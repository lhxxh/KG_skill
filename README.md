# Setup

1. Create conda environment

```bash
    conda create -n skill python=3.11
    conda activate skill
```

2. install python package

```bash
    pip install pypdf pdfplumber Pillow pdf2image reportlab pytesseract pypdfium2 pdf2image
```

3. Install neo4j on local and create instances.

4. change mcp server setting in .claude/settings.json.

5. Put paper under paper folder.

6. Put schema under schema folder.

# Run

1. Run this command in claude code to process paper into neo4j 

```bash
    Process the papers in paper/ using schema/pk_schema.md into Neo4j
```

2. Extracted Knowledge will be in ouput directory and also loaded into neo4j

3. Run an example in claude code to query neo4j 

```bash
    Query Neo4j: find all models that characterize alirocumab
```

# Test

1. open following url to check loaded KG into neo4j

```bash
    http://localhost:7474
```

# Other

1. Use following command to clear database in http://localhost:7474 if outdated data exist

```bash
    MATCH (n) DETACH DELETE n
```