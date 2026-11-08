# ADR-012: Status intervals and incidents rebuild in full

Date: 2026-11-08
Status: proposed

## Context

Section 6.1 of the plan lists every intermediate model as "Iceberg, incremental merge". The project rules also require incremental models to be idempotent: rerunning a day gives the same rows.

`int_line_minute_status` fits that well. Its grain is line x minute, each minute depends only on polls in that minute, and a run re-reads whole UTC days (ADR-008), so it merges on (line, minute).

`int_status_intervals` and `int_incidents` do not fit. Their rows are runs of minutes, and a run can start days before an incremental window:

- A windowed rebuild sees only the part of a run inside the window. It would write a second row starting at the window's first minute, beside the old row for the same run.
- Incident merging looks at the quiet minutes before and after each incident, so the window's edge would also change where incidents split.

A correct incremental version would need to find every run touching the window, re-read it from its true start, and delete the rows it replaces. The merge strategy doesn't delete (ADR-008).

## Decision

- `int_line_minute_status` stays an incremental merge, partitioned by UTC day.
- `int_status_intervals` and `int_incidents` are Iceberg tables rebuilt in full from `int_line_minute_status` on every run. So are `dim_service_hours`, `fct_line_daily` and `fct_line_weekly`, which the plan already lists as tables.

## Alternatives considered

- **Incremental merge with a wide lookback.** This is still wrong for any run longer than the lookback, such as a multi-day planned closure, and it fails quietly.
- **Incremental with delete-then-insert of every run that touches the window.** It's correct, but needs a hand-written pre-hook delete and leaves a window where readers see the runs missing (ADR-008 rejected this for staging).

## Consequences

- **Reruns are trivially idempotent:** each build recomputes these models from the minute table.
- **Cost grows with history.** A full rebuild reads all of `int_line_minute_status`, a few MB of Parquet a week at 20 lines. That stays well under a cent a run for years. Revisit if the daily build takes more than a few minutes or scans more than 1 GB.
- **Plan section 6.1** should say intermediate models are incremental where their grain allows, and rebuilt otherwise.
