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
# - An extra poll in a minute is already possible without reserved
#   concurrency (ADR-006); downstream counts covered minutes, not objects.
# - Two polls in one minute are about 170 TfL requests, under the 500 per
#   minute key limit.
# - The invoke starts at second 20 to 40 of a minute: scheduled polls
#   average about 3 s, so the minute's own poll has normally finished and,
#   once reserved concurrency is 1, the smoke poll does not take its slot.
#   If the poller is busy anyway, Lambda returns TooManyRequests, which is
#   reported as a warning, not a failure.
# - AWS_MAX_ATTEMPTS=1 and a read timeout longer than the function timeout
#   stop the CLI from re-sending a slow invoke, which would run it twice.
#
# A role without lambda:InvokeFunction gets a warning and exit 0, so the
# deploy stays green until the CI role grants it.
set -euo pipefail

poller="$1"
reference="$2"
bucket="$3"

export AWS_MAX_ATTEMPTS=1
export AWS_RETRY_MODE=standard
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# invoke <function> <read_timeout_s>: writes $work/response.json and
# $work/meta.json. Returns 0 on success, 3 when invoking is not allowed or
# throttled (warning printed), and fails the script on any other error.
invoke() {
  local fn="$1" read_timeout="$2"
  local payload
  payload=$(jq -cn --arg t "$(date -u +%Y-%m-%dT%H:%M:00Z)" '{scheduled_time: $t}')
  echo "invoking $fn with $payload"
  if ! aws lambda invoke \
    --function-name "$fn" \
    --cli-binary-format raw-in-base64-out \
    --payload "$payload" \
    --cli-read-timeout "$read_timeout" \
    "$work/response.json" >"$work/meta.json" 2>"$work/err.txt"; then
    if grep -q "AccessDenied" "$work/err.txt"; then
      echo "::warning title=smoke test skipped::The deploy role cannot invoke $fn yet (lambda:InvokeFunction). It is granted by the bootstrap re-apply (docs/runbook.md, H6); the smoke test runs once that is done."
      return 3
    fi
    if grep -q "TooManyRequests" "$work/err.txt"; then
      echo "::warning title=smoke test skipped::$fn was busy (throttled), most likely running its scheduled poll. Not a deploy failure; check the next run."
      return 3
    fi
    echo "::error title=smoke test failed::Invoking $fn failed."
    cat "$work/err.txt" >&2
    exit 1
  fi
  local function_error
  function_error=$(jq -r '.FunctionError // empty' "$work/meta.json")
  if [ -n "$function_error" ]; then
    echo "::error title=smoke test failed::$fn raised an error ($function_error): $(jq -r '"\(.errorType // "?"): \(.errorMessage // "?")"' "$work/response.json")"
    exit 1
  fi
  jq -c '{poll_id, polled_at, requests, request_failures, objects_written, duration_ms}' "$work/response.json"
}

# check_written <function>: fails unless the run wrote at least one object
# and that object is listed in the bucket.
check_written() {
  local fn="$1"
  local written poll_id polled_at feed prefix found
  written=$(jq -r '.objects_written // 0' "$work/response.json")
  if [ "$written" -lt 1 ]; then
    echo "::error title=smoke test failed::$fn ran but wrote no raw objects: $(jq -c '.feeds' "$work/response.json")"
    exit 1
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
    echo "::error title=smoke test failed::$fn reported $written object(s) but none with poll_id $poll_id is under s3://$bucket/$prefix."
    exit 1
  fi
  echo "found s3://$bucket/$(printf '%s\n' "$found" | head -n 1)"
}

second=$((10#$(date -u +%S)))
if [ "$second" -lt 20 ]; then
  sleep $((20 - second))
elif [ "$second" -gt 40 ]; then
  sleep $((80 - second))
fi

status=0
invoke "$poller" 90 || status=$?
[ "$status" -eq 3 ] && exit 0
check_written "$poller"
echo "smoke test: poller OK"

ref_count=$(aws s3api list-objects-v2 --bucket "$bucket" --prefix "raw/ref_" \
  --max-keys 1 --no-paginate --query KeyCount --output text)
if [ "$ref_count" != "0" ]; then
  echo "smoke test: reference data present in raw; not invoking $reference"
  exit 0
fi
# Reference timeout is 300 s.
status=0
invoke "$reference" 330 || status=$?
[ "$status" -eq 3 ] && exit 0
check_written "$reference"
echo "smoke test: reference OK"
