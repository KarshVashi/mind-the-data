# ADR-012: Declared-view intermediate models rebuild in full

Date: 2026-11-08
Status: proposed

## Context

Section 6.1 of the plan lists every intermediate model as "Iceberg, incremental merge". The project rules also require incremental models to be idempotent: rerunning a day gives the same rows. The M3 models don't fit that pattern, for two reasons.

**Runs cross window edges.** `int_status_intervals` and `int_incidents` hold runs of minutes, and a run can start days before an incremental window:

- A windowed rebuild sees only the part of a run inside the window. It would write a second row starting at the window's first minute, beside the old row for the same run.
- Incident merging looks at the quiet minutes either side of an incident, so the window's edge would also change where incidents split.
- A correct incremental version would have to re-read each touching run from its true start and delete the rows it replaces. A merge never deletes (ADR-008).

**Definitions live in seeds.** `int_line_minute_status` applies the severity weights, time bands, day types and operating hours. The metric decisions D16 to D22 are meant to change by editing a seed. An incremental merge would apply the new definition only to the last `lookback_days`. Older weeks would keep the old one, so the history would mix definitions without saying so. The same goes for a severity code added to the seed after it first appeared: older minutes would keep a null weight. Both failures are silent.

## Decision

These models are Iceberg tables, rebuilt in full from `raw_start_date` on every run:

- `int_line_minute_status`;
- `int_status_intervals`;
- `int_incidents`;
- `dim_service_hours`, `fct_line_daily` and `fct_line_weekly` (the plan already lists marts as tables).

The minute spine comes from `int_utc_minutes`, which needs no polls, so every line has every minute and gaps show as missing. Only the newest 30 minutes (`tail_trim_minutes`) are trimmed back to the latest staged poll, to cover the build-order race.

Staging stays an incremental merge (ADR-008). Raw is the expensive scan, and staging holds no metric definitions.

## Alternatives considered

- **Incremental merge with a wide lookback.** This is still wrong for any run, or seed edit, older than the lookback, and it fails quietly.
- **Incremental, plus a mandatory `--full-refresh` after every seed edit.** This works if people remember to do it. A forgotten refresh gives mixed definitions with no error.
- **Store seed-free facts incrementally, and apply the seeds in a table on top.** It's correct, but it adds a model and its tests, and saves little at this volume.

## Consequences

- **Idempotent by construction:** a rerun recomputes the same rows, and a seed edit reaches every past minute on the next build.
- **Cost grows with history.** Each build reads all of `stg_line_status` and `stg_polls` since `raw_start_date`, and writes about 10 million line-minutes a year. That is a few hundred MB a year scanned, well under a cent a run. Revisit if the daily build takes more than about 10 minutes or scans more than 1 GB. The fix then is the seed-free incremental layer above.
- **Unpartitioned.** Athena allows at most 100 partitions per CTAS. A table partitioned by day and rebuilt from `raw_start_date` would pass that around February 2027 and fall back to the adapter's batched path every day. Every downstream model reads the minute table whole, so it isn't partitioned; only the 7-day data tests lose pruning.
- **Backfill vars don't apply** to these models. Rebuilding staging days (runbook) is enough: the next build picks them up.
- **Plan section 6.1** should say intermediate models are rebuilt in full where their grain or their seeds require it.
