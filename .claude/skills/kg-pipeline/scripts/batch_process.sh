#!/usr/bin/env bash
#
# batch_process.sh — Process multiple PK papers through the KG pipeline
#
# Usage:
#   ./batch_process.sh <schema_path> <paper_dir_or_list>
#
# Arguments:
#   schema_path        Path to the extraction schema (e.g., schema/pk_schema.md)
#   paper_dir_or_list  Directory of PDFs, or a text file listing PDF paths (one per line)
#
# Features:
#   - JSONL log for resumability (skips already-processed papers)
#   - Collects failures for retry
#   - Rate limiting between papers
#
# Examples:
#   ./batch_process.sh schema/pk_schema.md paper/
#   ./batch_process.sh schema/pk_schema.md paper_list.txt

set -euo pipefail

# --- Configuration ---
DELAY_SECONDS=5          # Pause between papers (rate limiting)
LOG_FILE="kg_pipeline.log.jsonl"
FAILED_FILE="kg_pipeline_failed.txt"
MAX_RETRIES=1

# --- Argument parsing ---
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <schema_path> <paper_dir_or_list>"
    echo ""
    echo "  schema_path        Path to extraction schema (e.g., schema/pk_schema.md)"
    echo "  paper_dir_or_list  Directory of PDFs, or text file with one PDF path per line"
    exit 1
fi

SCHEMA_PATH="$1"
PAPER_SOURCE="$2"

if [[ ! -f "$SCHEMA_PATH" ]]; then
    echo "Error: Schema file not found: $SCHEMA_PATH"
    exit 1
fi

# --- Build paper list ---
declare -a PAPERS=()

if [[ -d "$PAPER_SOURCE" ]]; then
    # Directory of PDFs
    while IFS= read -r -d '' pdf; do
        PAPERS+=("$pdf")
    done < <(find "$PAPER_SOURCE" -name '*.pdf' -print0 | sort -z)
elif [[ -f "$PAPER_SOURCE" ]]; then
    # Text file with one path per line
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        PAPERS+=("$line")
    done < "$PAPER_SOURCE"
else
    echo "Error: Not a directory or file: $PAPER_SOURCE"
    exit 1
fi

TOTAL=${#PAPERS[@]}
if [[ $TOTAL -eq 0 ]]; then
    echo "No PDF files found."
    exit 0
fi

echo "Found $TOTAL papers to process."
echo "Schema: $SCHEMA_PATH"
echo "Log: $LOG_FILE"
echo ""

# --- Helper: check if paper was already processed ---
already_processed() {
    local pdf_path="$1"
    if [[ -f "$LOG_FILE" ]]; then
        grep -q "\"paper\":\"${pdf_path}\"" "$LOG_FILE" && \
        grep "\"paper\":\"${pdf_path}\"" "$LOG_FILE" | grep -q '"status":"success"'
    else
        return 1
    fi
}

# --- Helper: log result ---
log_result() {
    local pdf_path="$1"
    local status="$2"
    local message="$3"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "{\"timestamp\":\"$timestamp\",\"paper\":\"$pdf_path\",\"status\":\"$status\",\"message\":\"$message\"}" >> "$LOG_FILE"
}

# --- Process each paper ---
PROCESSED=0
SKIPPED=0
SUCCEEDED=0
FAILED=0

# Clear previous failed list
> "$FAILED_FILE"

for pdf_path in "${PAPERS[@]}"; do
    PROCESSED=$((PROCESSED + 1))

    # Skip if already processed
    if already_processed "$pdf_path"; then
        echo "[$PROCESSED/$TOTAL] SKIP (already done): $pdf_path"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Check file exists
    if [[ ! -f "$pdf_path" ]]; then
        echo "[$PROCESSED/$TOTAL] MISSING: $pdf_path"
        log_result "$pdf_path" "error" "File not found"
        echo "$pdf_path" >> "$FAILED_FILE"
        FAILED=$((FAILED + 1))
        continue
    fi

    echo "[$PROCESSED/$TOTAL] Processing: $pdf_path"

    # Invoke claude CLI with the kg-pipeline skill
    ATTEMPT=0
    SUCCESS=false
    while [[ $ATTEMPT -le $MAX_RETRIES ]]; do
        if claude --skill kg-pipeline --print \
            "Process this paper into the knowledge graph using schema $SCHEMA_PATH: $pdf_path" \
            2>&1; then
            SUCCESS=true
            break
        fi
        ATTEMPT=$((ATTEMPT + 1))
        if [[ $ATTEMPT -le $MAX_RETRIES ]]; then
            echo "  Retry $ATTEMPT/$MAX_RETRIES..."
            sleep 2
        fi
    done

    if $SUCCESS; then
        echo "[$PROCESSED/$TOTAL] SUCCESS: $pdf_path"
        log_result "$pdf_path" "success" "Processed successfully"
        SUCCEEDED=$((SUCCEEDED + 1))
    else
        echo "[$PROCESSED/$TOTAL] FAILED: $pdf_path"
        log_result "$pdf_path" "error" "Processing failed after $((MAX_RETRIES + 1)) attempts"
        echo "$pdf_path" >> "$FAILED_FILE"
        FAILED=$((FAILED + 1))
    fi

    # Rate limiting
    if [[ $PROCESSED -lt $TOTAL ]]; then
        sleep "$DELAY_SECONDS"
    fi
done

# --- Summary ---
echo ""
echo "========================================="
echo "Batch Processing Complete"
echo "========================================="
echo "Total papers:  $TOTAL"
echo "Skipped:       $SKIPPED"
echo "Succeeded:     $SUCCEEDED"
echo "Failed:        $FAILED"
echo ""
echo "Log:     $LOG_FILE"
if [[ $FAILED -gt 0 ]]; then
    echo "Failures: $FAILED_FILE"
    echo ""
    echo "To retry failed papers:"
    echo "  $0 $SCHEMA_PATH $FAILED_FILE"
fi
