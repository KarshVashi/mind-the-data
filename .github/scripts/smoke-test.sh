#!/usr/bin/env bash
# Post-deploy smoke test: invoke the poller once, synchronously, with the
# event its schedule sends, and confirm the raw object it wrote is in S3. If
# raw holds no reference data yet, invoke the reference function once too.
#
# Usage: smoke-test.sh <poller_function> <reference_function> <raw_bucket>
#
# Safe to run alongside the schedule:
# - Every poll gets its own poll_id (a uuid) in every object key, and the
#   writer uses IfNoneMatch="*", so a smoke poll can never overwrite or
#   collide with a scheduled poll's object. It adds one extra poll's objects.
# - An extra poll in a minute is already possible while the poller has no
#   reserved concurrency (ADR-006).
# - Two polls in one minute are about 170 TfL requests, under the 500 per
#   minute key limit.
# - The invoke starts at second 20 to 40 of a minute. Scheduled polls
#   average about 3 s, so the minute's own poll has normally finished, and
#   with reserved concurrency 1 (ADR-006, once the quota allows) the smoke
#   poll does not take its slot. A throttled invoke never ran, so it is
#   retried once a minute later; throttled twice fails the deploy, since a
#   poller that is always throttled cannot collect.
# - AWS_MAX_ATTEMPTS=1 and a read timeout longer than the function timeout
#   stop the CLI from re-sending a slow invoke, which would run it twice.
#
# Only a role that is not allowed lambda:InvokeFunction gets a warning and
# exit 0, so the deploy stays green if the CI role's Lambda rights are
# narrowed before invoke is granted explicitly. Every other error fails.
set -euo pipefail

poller="$1"
reference="$2"
bucket="$3"

export AWS_MAX_ATTEMPTS=1
export AWS_RETRY_MODE=standard
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fail() {
  echo "::error title=smoke test failed::$1"
  exit 1
}

# Sleep until second 20 to 40 of a minute; with "next", into the next minute.
wait_for_window() {
  local second
  second=$((10#$(date -u +%S)))
  if [ "${1:-}" = next ]; then
    sleep $((90 - second))
  elif [ "$second" -lt 20 ]; then
    sleep $((20 - second))
  elif [ "$second" -gt 40 ]; then
    sleep $((80 - second))
  fi
}

# invoke <function> <read_timeout_s>: on success writes $work/response.json
# and sets skipped=0. Sets skipped=1 when the role may not invoke. Fails the
# script on any other error.
skipped=0
invoke() {
  local fn="$1" read_timeout="$2" attempt payload function_error
  for attempt in 1 2; do
    payload=$(jq -cn --arg t "$(date -u +%Y-%m-%dT%H:%M:00Z)" '{scheduled_time: $t}') ||
      fail "could not build the invoke payload"
    echo "invoking $fn with $payload"
    if aws lambda invoke \
      --function-name "$fn" \
      --cli-binary-format raw-in-base64-out \
      --payload "$payload" \
      --cli-read-timeout "$read_timeout" \
      "$work/response.json" >"$work/meta.json" 2>"$work/err.txt"; then
      break
    fi
    cat "$work/err.txt" >&2
    if grep -q "(AccessDeniedException)" "$work/err.txt" &&
      grep -q "lambda:InvokeFunction" "$work/err.txt"; then
      echo "::warning title=smoke test skipped::The deploy role is not allowed lambda:InvokeFunction on $fn. Grant it in infra/modules/ci_role and re-apply the bootstrap (docs/runbook.md, H6); the smoke test runs once that is done."
      skipped=1
      return 0
    fi
    if [ "$attempt" -eq 1 ] && grep -q "(TooManyRequestsException)" "$work/err.txt"; then
      echo "$fn throttled, most likely running its scheduled poll; retrying next minute"
      wait_for_window next
      continue
    fi
    fail "invoking $fn failed (see the log above)"
  done
  function_error=$(jq -r '.FunctionError // empty' "$work/meta.json") ||
    fail "unreadable invoke metadata for $fn"
  if [ -n "$function_error" ]; then
    fail "$fn raised an error ($function_error): $(jq -r '"\(.errorType // "?"): \(.errorMessage // "?")"' "$work/response.json" 2>/dev/null || echo unreadable response)"
  fi
  jq -c '{poll_id, polled_at, requests, request_failures, objects_written, duration_ms}' \
    "$work/response.json" || fail "unreadable response from $fn"
  skipped=0
}

# check_written <function>: fails unless the run wrote at least one object
# and that object is listed in the bucket.
check_written() {
  local fn="$1" written poll_id polled_at feed prefix found
  written=$(jq -r '.objects_written // 0' "$work/response.json")
  if [ "$written" -lt 1 ]; then
    fail "$fn ran but wrote no raw objects: $(jq -c '.feeds' "$work/response.json")"
  fi
  poll_id=$(jq -r '.poll_id' "$work/response.json")
  polled_at=$(jq -r '.polled_at' "$work/response.json")
  feed=$(jq -r '[.feeds | to_entries[] | select(.value.written) | .key][0]' "$work/response.json")
  # Keys are raw/<feed>/dt=YYYY-MM-DD/hour=HH/<feed>_<stamp>_<poll_id>.ndjson.gz
  # (src/mind_the_data/common/raw.py), from polled_at in UTC.
  prefix="raw/$feed/dt=${polled_at:0:10}/hour=${polled_at:11:2}/"
  found=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix "$prefix" \
    --query "Contents[?contains(Key, '$poll_id')].Key" --output text)
  if [ -z "$found" ] || [ "$found" = "None" ]; then
    fail "$fn reported $written object(s) but none with poll_id $poll_id is under s3://$bucket/$prefix."
  fi
  echo "found s3://$bucket/$(printf '%s\n' "$found" | head -n 1)"
}

wait_for_window
invoke "$poller" 90
if [ "$skipped" -eq 1 ]; then
  exit 0
fi
check_written "$poller"
echo "smoke test: poller OK"

ref_count=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix "raw/ref_" \
  --max-keys 1 --no-paginate --query KeyCount --output text)
if [ "$ref_count" != "0" ]; then
  echo "smoke test: reference data present in raw; not invoking $reference"
  exit 0
fi
# Reference timeout is 300 s.
invoke "$reference" 330
if [ "$skipped" -eq 1 ]; then
  exit 0
fi
check_written "$reference"
echo "smoke test: reference OK"
