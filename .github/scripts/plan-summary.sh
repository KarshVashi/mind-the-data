#!/usr/bin/env bash
# Writes a Terraform plan to the job log and the run summary.
# Usage: plan-summary.sh <plan.txt> <heading>
set -euo pipefail

plan_file="$1"
heading="$2"
max_lines=400
lines=$(wc -l < "$plan_file")

cat "$plan_file"

{
  echo "### $heading"
  echo '```'
  head -n "$max_lines" "$plan_file"
  echo '```'
  if [ "$lines" -gt "$max_lines" ]; then
    echo "Truncated: showing $max_lines of $lines lines. The full plan is in the job log."
  fi
} >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
