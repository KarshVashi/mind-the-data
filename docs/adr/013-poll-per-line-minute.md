# ADR-013: Read the latest poll that lists the line, and only current entries

Date: 2026-11-08
Status: proposed

## Context

ADR-004 reads, for each line and minute, "the entries from the latest successful poll in that minute", and makes the minute missing when "a successful poll returned no entry for the line". It was written when one poll per minute was the norm. Since then:

- **More than one poll per minute.** A minute can hold two polls: the post-deploy smoke test (runbook H10, D12) adds one, and retries are kept as separate polls (ADR-003).
- **Expired entries.** The week-one data shows TfL still listing a planned entry for a minute or two after its validity period has ended, with no other entry for the line. It happens 1 to 5 line-minutes a day.

Read literally, ADR-004 makes a minute missing whenever the minute's latest poll happens to omit a line, even when an earlier poll in the same minute listed it.

## Decision

This amends rules 1 and 4 of ADR-004. For each line and minute:

1. **Which poll.** Read the latest successful poll in the minute that lists the line. Ties on `polled_at` go to the higher `poll_id`, then `request_path`, so the choice is deterministic.
2. **Which entries.** An entry of that poll counts when it has no validity period, or one of its periods overlaps the minute (`valid_from` before the minute's end, `valid_to` after its start).
3. **Missing.** The minute is missing, with a reason, when:
   - no successful poll started in the minute (`no_successful_poll`);
   - no successful poll in the minute lists the line (`line_not_in_poll`);
   - the chosen poll lists the line but none of its entries is current (`no_current_entry`). An earlier poll in the same minute is not used as a fallback.

Rules 2, 3 and 5 of ADR-004 are unchanged.

## Alternatives considered

- **Only the very latest poll, as ADR-004 reads.** This throws away a valid status whenever a second poll omits a line.
- **Fall back to an earlier poll when the latest has no current entry.** This mixes two polls' views of one minute. The case is rare and the minute is marked missing, which never flatters a line.
- **Treat a just-expired entry as current.** TfL's own period says it has ended, so this would guess.

## Consequences

- Duplicate polls never make a minute missing, so the smoke test needs no special handling downstream.
- About 1 to 5 line-minutes a day are missing because of expired entries. They lower completeness slightly and never count as Good Service.
- Plan section 6.2 (`int_line_minute_status`) should read "the latest successful poll that lists the line".
