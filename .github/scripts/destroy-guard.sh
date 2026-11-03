#!/usr/bin/env bash
# Fails when a Terraform plan deletes or replaces any resource, unless allowed.
# Usage: destroy-guard.sh <plan.json> <true|false>
set -euo pipefail

plan_json="$1"
allow="${2:-false}"

# A replacement shows as ["delete","create"] or ["create","delete"].
removals=$(jq -r '[.resource_changes[]? | select(.change.actions | index("delete")) | .address] | .[]' "$plan_json")

if [ -z "$removals" ]; then
  echo "destroy guard: no resources deleted or replaced"
  exit 0
fi

count=$(printf '%s\n' "$removals" | wc -l | tr -d ' ')
list=$(printf '%s\n' "$removals" | sed 's/^/  - /')

if [ "$allow" = "true" ]; then
  echo "::warning title=destroy allowed::$count resource(s) will be deleted or replaced."
  printf '%s\n' "$list"
  exit 0
fi

echo "::error title=destroy blocked::$count resource(s) would be deleted or replaced. Add [allow-destroy] to the pull request title before merging, or re-run manually with allow_destroy."
printf '%s\n' "$list"
exit 1
