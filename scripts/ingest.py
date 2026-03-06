#!/usr/bin/env python3
"""
ingest.py — Master batch orchestrator for the KG pipeline.

End-to-end pipeline: PDF → extraction JSON → Neo4j loading.
Extraction uses Claude CLI with the kg-extract skill (LLM).
Loading uses load_graph.py (deterministic, no LLM).

Schema-driven: works with any schema, not hardcoded to a specific domain.

Usage:
    python3 scripts/ingest.py                      # all PDFs in paper/
    python3 scripts/ingest.py paper/specific.pdf   # one PDF
    python3 scripts/ingest.py --skip-extraction    # load existing JSONs only
    python3 scripts/ingest.py --skip-loading       # extract only
    python3 scripts/ingest.py --init-schema        # create constraints/indexes only
    python3 scripts/ingest.py --schema schema/my_schema.md  # use a different schema
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# --- Configuration ---

PAPER_DIR = Path("paper")
OUTPUT_DIR = Path("output")
DEFAULT_SCHEMA = Path("schema/pk_schema.md")
LOAD_SCRIPT = Path("scripts/load_graph.py")


def get_pdf_stem(pdf_path):
    """Get the stem of a PDF filename (without extension)."""
    return Path(pdf_path).stem


def extraction_json_path(pdf_path):
    """Get the expected output JSON path for a PDF."""
    return OUTPUT_DIR / f"{get_pdf_stem(pdf_path)}_extraction.json"


# --- Schema Init ---


def init_schema(schema_path):
    """Run load_graph.py --init-schema to create constraints and indexes."""
    print("=== Initializing Schema ===")
    result = subprocess.run(
        ["python3", str(LOAD_SCRIPT), "--schema", str(schema_path), "--init-schema"],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print("[ERROR] Schema initialization failed", file=sys.stderr)
        return False
    return True


# --- Extraction ---


def extract_paper(pdf_path, schema_path):
    """Call Claude CLI with kg-extract skill to extract entities from a PDF."""
    pdf_path = Path(pdf_path)
    output_path = extraction_json_path(pdf_path)

    if output_path.exists():
        print(f"  [SKIP] Extraction JSON already exists: {output_path}")
        return True

    print(f"  [EXTRACT] {pdf_path.name} ...")

    prompt = (
        f"Extract all entities and relationships from the paper "
        f"at {pdf_path} using the schema at {schema_path}. "
        f"Write the extraction JSON to {output_path}. "
        f"Follow the kg-extract skill instructions exactly."
    )

    result = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--dangerously-skip-permissions",
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 minute timeout per paper
    )

    if result.returncode != 0:
        print(f"  [ERROR] Claude extraction failed for {pdf_path.name}", file=sys.stderr)
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}", file=sys.stderr)
        return False

    # Validate output exists and is valid JSON
    if not output_path.exists():
        print(f"  [ERROR] Expected output not found: {output_path}", file=sys.stderr)
        return False

    try:
        with open(output_path) as f:
            data = json.load(f)
        assert "source_paper" in data, "Missing source_paper"
        assert "entities" in data, "Missing entities"
        assert "relationships" in data, "Missing relationships"
        assert len(data["entities"]) > 0, "No entities extracted"
        print(f"  [OK] Extracted {len(data['entities'])} entities, {len(data['relationships'])} relationships")
        return True
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"  [ERROR] Invalid extraction JSON: {e}", file=sys.stderr)
        return False


# --- Loading ---


def load_paper(json_path, schema_path):
    """Call load_graph.py to load an extraction JSON into Neo4j."""
    json_path = Path(json_path)

    if not json_path.exists():
        print(f"  [ERROR] JSON not found: {json_path}", file=sys.stderr)
        return False

    print(f"  [LOAD] {json_path.name} ...")
    result = subprocess.run(
        ["python3", str(LOAD_SCRIPT), "--schema", str(schema_path), str(json_path)],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [ERROR] Loading failed for {json_path.name}", file=sys.stderr)
        return False
    return True


# --- Main Pipeline ---


def process_paper(pdf_path, schema_path, skip_extraction=False, skip_loading=False):
    """Process a single paper through the full pipeline."""
    pdf_path = Path(pdf_path)
    json_path = extraction_json_path(pdf_path)
    print(f"\n--- Processing: {pdf_path.name} ---")

    # Step 1: Extraction (LLM)
    if not skip_extraction:
        if not extract_paper(pdf_path, schema_path):
            return False
    else:
        if not json_path.exists():
            print(f"  [SKIP] No extraction JSON found (extraction skipped): {json_path}")
            return False
        print(f"  [SKIP] Extraction skipped, using: {json_path}")

    # Step 2: Loading (deterministic)
    if not skip_loading:
        if not load_paper(json_path, schema_path):
            return False
    else:
        print(f"  [SKIP] Loading skipped")

    print(f"  [DONE] {pdf_path.name}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end KG pipeline: PDF → JSON → Neo4j"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="PDF file(s) or directory. Defaults to paper/ directory.",
    )
    parser.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA),
        help=f"Path to schema markdown file (default: {DEFAULT_SCHEMA})",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip extraction, load existing JSONs only",
    )
    parser.add_argument(
        "--skip-loading",
        action="store_true",
        help="Skip Neo4j loading, extract only",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Create Neo4j constraints/indexes only (no processing)",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {args.schema}", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Always init schema first (idempotent)
    if not args.skip_loading:
        init_schema(schema_path)
    elif args.init_schema:
        init_schema(schema_path)
        if not args.inputs:
            return

    if args.init_schema and not args.inputs:
        print("Schema initialized. Pass PDF paths or remove --init-schema to process papers.")
        return

    # Resolve input files
    pdf_files = []
    if not args.inputs:
        if PAPER_DIR.exists():
            pdf_files = sorted(PAPER_DIR.glob("*.pdf"))
        if not pdf_files:
            print(f"[ERROR] No PDFs found in {PAPER_DIR}/", file=sys.stderr)
            sys.exit(1)
    else:
        for inp in args.inputs:
            p = Path(inp)
            if p.is_dir():
                pdf_files.extend(sorted(p.glob("*.pdf")))
            elif p.suffix == ".pdf":
                pdf_files.append(p)
            elif p.suffix == ".json" and args.skip_extraction:
                pdf_files.append(p)
            else:
                print(f"[WARN] Skipping: {inp}", file=sys.stderr)

    if not pdf_files:
        print("[ERROR] No files to process", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== KG Pipeline: {len(pdf_files)} file(s) to process ===")
    print(f"  Schema: {schema_path}")
    if args.skip_extraction:
        print("  Mode: Loading only (extraction skipped)")
    elif args.skip_loading:
        print("  Mode: Extraction only (loading skipped)")
    else:
        print("  Mode: Full pipeline (extraction + loading)")

    # Process each paper
    succeeded = []
    failed = []

    for pdf_path in pdf_files:
        try:
            if pdf_path.suffix == ".json" and args.skip_extraction:
                print(f"\n--- Loading: {pdf_path.name} ---")
                if load_paper(pdf_path, schema_path):
                    succeeded.append(str(pdf_path))
                else:
                    failed.append(str(pdf_path))
            else:
                if process_paper(pdf_path, schema_path, args.skip_extraction, args.skip_loading):
                    succeeded.append(str(pdf_path))
                else:
                    failed.append(str(pdf_path))
        except Exception as e:
            print(f"[ERROR] {pdf_path}: {e}", file=sys.stderr)
            failed.append(str(pdf_path))

    # Final summary
    print("\n" + "=" * 50)
    print("=== Pipeline Summary ===")
    print(f"  Succeeded: {len(succeeded)}/{len(pdf_files)}")
    print(f"  Failed:    {len(failed)}/{len(pdf_files)}")

    if succeeded:
        print(f"\n  Succeeded:")
        for s in succeeded:
            print(f"    + {s}")

    if failed:
        print(f"\n  Failed:")
        for f in failed:
            print(f"    - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
