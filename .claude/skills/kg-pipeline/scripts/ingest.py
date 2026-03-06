#!/usr/bin/env python3
"""
ingest.py — Batch orchestrator for the KG pipeline.

End-to-end pipeline: PDF → extraction JSON → Neo4j loading.
Extraction uses Claude CLI with the kg-extract skill (LLM).
Loading uses load_graph.py (deterministic, no LLM).

Usage:
    python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/ schema/pk_schema.md
    python3 .claude/skills/kg-pipeline/scripts/ingest.py paper/specific.pdf schema/pk_schema.md
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# --- Configuration ---

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path("output")
LOAD_SCRIPT = SCRIPT_DIR / "load_graph.py"


def get_pdf_stem(pdf_path):
    """Get the stem of a PDF filename (without extension)."""
    return Path(pdf_path).stem


def extraction_json_path(pdf_path):
    """Get the expected output JSON path for a PDF."""
    return OUTPUT_DIR / f"{get_pdf_stem(pdf_path)}_extraction.json"


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

    # Unset CLAUDECODE env var to allow nested Claude sessions
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    result = subprocess.run(
        [
            "claude",
            "-p", prompt,
            "--dangerously-skip-permissions",
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 minute timeout per paper
        env=env,
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


def load_paper(json_path):
    """Call load_graph.py to load an extraction JSON into Neo4j."""
    json_path = Path(json_path)

    if not json_path.exists():
        print(f"  [ERROR] JSON not found: {json_path}", file=sys.stderr)
        return False

    print(f"  [LOAD] {json_path.name} ...")
    result = subprocess.run(
        ["python3", str(LOAD_SCRIPT), str(json_path)],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [ERROR] Loading failed for {json_path.name}", file=sys.stderr)
        return False
    return True


# --- Main Pipeline ---


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end KG pipeline: PDF → JSON → Neo4j"
    )
    parser.add_argument(
        "paper",
        help="PDF file or directory of PDFs to process",
    )
    parser.add_argument(
        "schema",
        help="Path to schema file",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {args.schema}", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Resolve input files
    pdf_files = []
    p = Path(args.paper)
    if p.is_dir():
        pdf_files = sorted(p.glob("*.pdf"))
    elif p.suffix == ".pdf" and p.exists():
        pdf_files = [p]
    else:
        print(f"[ERROR] Not a valid PDF file or directory: {args.paper}", file=sys.stderr)
        sys.exit(1)

    if not pdf_files:
        print(f"[ERROR] No PDFs found in {args.paper}", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== KG Pipeline: {len(pdf_files)} file(s) to process ===")
    print(f"  Schema: {schema_path}")

    # Phase 1: Parallel extraction (LLM)
    print("\n--- Phase 1: Extraction (parallel) ---")
    extract_succeeded = []
    extract_failed = []

    with ThreadPoolExecutor(max_workers=len(pdf_files)) as executor:
        futures = {
            executor.submit(extract_paper, pdf_path, schema_path): pdf_path
            for pdf_path in pdf_files
        }
        for future in as_completed(futures):
            pdf_path = futures[future]
            try:
                if future.result():
                    extract_succeeded.append(pdf_path)
                else:
                    extract_failed.append(pdf_path)
            except Exception as e:
                print(f"[ERROR] {pdf_path}: {e}", file=sys.stderr)
                extract_failed.append(pdf_path)

    # Phase 2: Sequential loading (entity resolution depends on prior loads)
    print("\n--- Phase 2: Loading (sequential) ---")
    succeeded = []
    failed = list(map(str, extract_failed))

    for pdf_path in pdf_files:
        if pdf_path in extract_failed:
            continue
        json_path = extraction_json_path(pdf_path)
        print(f"\n--- Loading: {pdf_path.name} ---")
        try:
            if load_paper(json_path):
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
