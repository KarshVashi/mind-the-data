# ADR-004: Multiple statuses per line

Date: 2026-11-02
Status: accepted

## Context

TfL's line status feed can return several status entries for one line at once. Examples:

- "Minor Delays" between two stations alongside "Part Closure" on a branch.
- A current status and a planned one with a later validity period.

Each entry carries a severity code, a reason, and validity periods. The Disruption Index needs exactly one status per line per minute.

## Decision

**Worst status wins.** For each line and minute:

- Take every status entry whose validity period covers that minute.
- Keep the one with the lowest `statusSeverity`. In TfL's scale, lower means worse, and 10 is Good Service.
- That status sets the minute's severity weight, its reason text, and whether it is planned or unplanned.
- If no successful poll covers the minute, it is flagged `is_missing` and left out of both the numerator and the denominator. It is never treated as Good Service.

Every status entry is kept in `stg_line_status`, at a grain of poll × line × status entry. The reduction happens in `int_line_minute_status`, so the choice can be revisited without re-ingesting anything.

## Alternatives considered

- **Sum the weights of concurrent statuses.** This double-counts one incident that TfL describes in two entries, and penalises lines whose updates are more detailed.
- **Use the first entry in the response.** TfL's response order is not documented, and could change without notice.
- **Weight each status by the share of the line it affects.** This is fairer for part suspensions, but needs reliable station ranges for each status. It is deferred to phase 2 as affected-section scaling.

## Consequences

- **Simple and easy to explain:** the methodology can state the rule in one sentence.
- **Planned works stay separate:** when a planned closure and an unplanned delay overlap, the worse severity sets the minute. Planned Closure and Part Closure carry zero weight, so they never hide an unplanned delay and are reported separately as engineering works.
- **Part suspensions are flat:** a part suspension of two stations counts the same as one of twenty. This is a known limitation, stated in the methodology.
- **Unknown codes fail:** an unknown severity code fails a dbt test rather than defaulting silently.
