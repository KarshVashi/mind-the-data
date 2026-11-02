# ADR-004: Multiple statuses per line

Date: 2026-11-02
Status: proposed (changes the plan's "lowest severity code" rule; needs sign-off)

## Context

TfL's line status feed can return several status entries for one line at once. Examples:

- "Severe Delays" on one section alongside a planned "Part Closure" on a branch.
- A current status and a planned one with a later validity period.

Each entry carries a `statusSeverity` code, a reason, and validity periods, which are often empty for Good Service. The Disruption Index needs exactly one severity weight per line per minute.

The plan's first wording was "worst status wins, taken as the lowest severity code". That does not work, because TfL's codes are not ordered by how bad they are:

| Code | Status | Weight |
| --- | --- | --- |
| 0 | Special Service | 1 |
| 2 | Suspended | 8 |
| 3 | Part Suspended | 5 |
| 4 | Planned Closure | 0 |
| 5 | Part Closure | 0 |
| 6 | Severe Delays | 3 |
| 7 | Reduced Service | 1.5 |
| 8 | Bus Service | 0 when planned, otherwise 5 |
| 9 | Minor Delays | 1 |
| 10 | Good Service | 0 |

TfL's `Line/Meta/Severity` also lists codes the plan has no weight for: 1 Closed, and 11 to 20 (Part Closed, Exit Only, No Step Free Access, Change of frequency, Diverted, Not Running, Issues Reported, No Issues, Information, Service Closed). The weights seed in M3 must cover every code that the week-one review finds on line status.

Under lowest-code-wins, a Part Closure (code 5) overlapping Severe Delays (code 6) would pick the closure. The minute would score 0 and move to engineering works, so the delay would disappear from the index. Special Service (code 0) would also beat Suspended (code 2).

## Decision

**Worst status wins, where "worst" means the highest severity weight.** For each line and minute:

1. **Which entries count.** These are the entries from the latest successful poll in that minute:
   - Entries whose validity period covers the minute.
   - Entries with no validity periods. These apply to the minute of the poll that returned them.
2. **Pick the worst.** Take the entry with the highest weight from `seeds/severity_weights.csv`. On a tie, the lowest code wins, then the reason text in alphabetical order, so the result is deterministic. That entry sets the minute's weight, its reason and its planned or unplanned flag.
3. **Planned closures.** A minute counts as engineering works, and is left out of the reliability score, only when the chosen entry is a planned closure (Planned Closure or Part Closure) and no other entry has a weight above 0. Any minute with a closure entry is still counted in `rpt_engineering_works`.
4. **Missing data.** A minute is flagged `is_missing`, and left out of both the numerator and the denominator, in either case:
   - No successful poll covers the minute.
   - A successful poll returned no entry for the line.

   It is never treated as Good Service.
5. **Unknown codes.** An unknown severity code fails a dbt test rather than defaulting silently.

Every status entry is kept in `stg_line_status`, at a grain of poll × line × status entry. The reduction happens in `int_line_minute_status`, so the rule can change without re-ingesting anything.

## Alternatives considered

- **The lowest severity code, as the plan first said.** Rejected for the reasons above.
- **Sum the weights of concurrent statuses.** This double-counts one incident that TfL describes in two entries.
- **Weight each status by the share of the line it affects.** This is deferred to phase 2 as affected-section scaling.

## Consequences

- **Delays always count:** an unplanned delay is never hidden by an overlapping planned closure.
- **Part suspensions are flat:** a part suspension of two stations counts the same as one of twenty. This is a known limitation, stated in the methodology.
- **Plan wording:** the plan's description of `int_line_minute_status` (section 6.2) and ADR-004's row in its fairness rules need the same correction once this is accepted.
