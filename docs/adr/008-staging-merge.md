# ADR-008: Staging models merge whole days on a row key

Date: 2026-11-03
Status: proposed

## Context

Section 6.1 of the plan lists staging as "Iceberg, incremental append, partitioned by day". The project rules also require every incremental model to be idempotent: re-running a day must give the same result.

Append is not idempotent. Raw objects for a day keep arriving until the day ends, and a rerun after a failed build, or a backfill, reads days that are already loaded. With append, every rerun inserts the same rows again, and downstream counts double.

Raw is replayable and holds every request, failed ones included (ADR-003), so staging can always be rebuilt from it. Two things make a rebuild cheap enough to do every day:

- **Bounded reads.** Each run reads a known set of whole UTC days, never the full raw prefix.
- **A key per row.** Each row has a key that comes from the raw data alone.

## Decision

- **Merge, not append.** Staging models are Iceberg tables, incremental, with `incremental_strategy: merge`.
- **A row key per model.** Each model has a key column, `<model>_key`: an md5 of the columns that make up its grain. Every grain starts with `(poll_id, request_path)`, then adds:
  - TfL's own ids, where it has them, such as `prediction_id` or `line_id`;
  - otherwise the 1-based position in the response, taken from `unnest ... with ordinality`.

  The merge matches on this key, and the uniqueness test runs on it. Rows that repeat within one poll are deduplicated before the merge, because Iceberg's MERGE fails when two source rows match one target row.
- **Partitioned by `dt`.** Tables are partitioned by `dt`, a `date` column: the UTC date of the raw partition, which is also the UTC date of `polled_at`. We use an identity partition on a plain column, rather than `day(polled_at)`, for two reasons:
  - downstream filters and `incremental_predicates` can name the column directly;
  - it matches the raw partition one to one.
- **Which days a run reads** (`macros/staging_window.sql`). Every run reads whole raw days only, so a rerun rebuilds the same rows:
  - **Full refresh:** from `raw_start_date` to today.
  - **Incremental run:** the last `lookback_days` days (default 2) up to today.
  - **Backfill:** `start_date` and `end_date`, inclusive, which override both. Combining them with `--full-refresh` is a compile error, because the rebuilt table would hold only that window.

  The bounds are rendered as literals, so Athena's partition projection can prune raw `dt` before it lists S3.
- **Limiting the merge scan.** `incremental_predicates` stops the merge scanning the whole target table. Its default is `target.dt >= current_date - (lookback_days + 1) days`. The predicate is resolved when the project is parsed, so it cannot see the window computed at run time. The extra day covers a run that crosses midnight UTC.

## Alternatives considered

- **Append, as the plan said.** It is not idempotent, as described above.
- **Delete then insert per day.** dbt-athena has no Iceberg `insert_overwrite`. A hand-written pre-hook delete would also leave a window where the day is empty for readers.
- **Merge on the natural column list instead of a hash.** This also works. A single key column keeps the merge condition, the uniqueness test and later joins the same everywhere, without a test package for multi-column uniqueness.

## Consequences

- **Reruns are safe.** Rerunning, or backfilling, any range of days gives the same rows.
- **Deleted rows are not removed.** A merge never deletes. If a parser change starts dropping rows, the old rows stay until a full refresh. Changing a model's logic therefore needs a `--full-refresh`, which is cheap at staging's size.
- **Reruns cost more.** Each daily run rescans the last `lookback_days` raw days, not just the new hours, so it reads about twice the raw bytes it strictly needs. Check this against the workgroup's per-query scan limit once a week of real data exists.
- **stg_polls reads every feed again.** Each run reads every raw feed twice over the window: once for its feed model and once for `stg_polls`. This is acceptable at this volume.
- **Section 6.1 of the plan** should read "incremental merge" for staging.

## Related grain decision

The plan gives `stg_line_status` a grain of poll × line × status entry. Staging instead adds validity period to that grain:

- An entry with several periods gives one row per period, with `valid_from` and `valid_to`.
- An entry with no periods gives one row with null period columns.

`int_line_minute_status` needs to know, per entry, whether a period covers a minute. With one row per period, that is a plain range join, with no array handling. The plan's grain is still available by taking distinct (poll, line, status_ordinal).
