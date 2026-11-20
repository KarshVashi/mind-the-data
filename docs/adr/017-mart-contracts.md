# ADR-017: Enforced contracts on the marts that publishing reads

Date: 2026-11-20
Status: proposed

## Context

`src/mind_the_data/publish/marts.py` selects fixed column lists from the dbt marts. A renamed or retyped column surfaced only when the export ran, after the dbt build had passed. No model had a contract.

dbt-athena-community 1.11.1 supports `contract: {enforced: true}` on table models, including Iceberg. `athena__create_table_as` calls `get_assert_columns_equivalent` before it creates the table, so a mismatch fails the model before anything is written. Athena has no constraints (`CONSTRAINT_SUPPORT` is all `NOT_SUPPORTED`), so a contract checks column names and broad types only (`varchar`, `bigint`, `double`, `date`, `timestamp`, `boolean`, `array`), not nullability. The `not_null` and `unique` data tests stay as they are.

Checked on 2026-11-20: a dev build of `dim_lines` with the contract passes. With `is_low_volume` declared `varchar` it fails with `data type mismatch` (actual `BOOLEAN`).

## Decision

Enforce contracts, with a `data_type` on every column, on the table models `publish/marts.py` reads: `dim_lines`, `dim_station`, `fct_line_daily`, `fct_line_weekly`, `fct_line_weekly_measured`, `fct_line_measured_daily`, `fct_station_disruption_weekly`, `fct_station_weekly_measured`, `rpt_monday_morning`, `rpt_division_seeding`, `rpt_divisions_monthly`, `rpt_season`, `rpt_league_strikes_excluded` and `rpt_weekly_status_mix`.

`tests/unit/test_dbt_contracts.py` parses the export's own SELECTs and fails when a mart it reads has no enforced contract, or when a column it reads is not declared. A new published mart therefore cannot skip the contract.

## Alternatives considered

- Contracts on every model. Staging and intermediate models are incremental merges, where the adapter does not run the contract check in the same place, and they change often; the cost outweighs the gain.
- The pytest check alone. It would catch undeclared columns but not a type change in the SQL.

## Consequences

- Adding or retyping a column in a contracted mart needs the same change in `_schema.yml`, or the build fails. That is the point.
- `timestamp(6)` columns are declared `timestamp`; the adapter compares the part before the parenthesis.
- Revisit if dbt-athena changes how contracts compare types, or if a published mart becomes incremental.
