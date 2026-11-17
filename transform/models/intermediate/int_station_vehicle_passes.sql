{{
    config(
        tags=['station'],
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='vehicle_pass_key',
        partitioned_by=['london_date'],
        on_schema_change='fail',
        incremental_predicates=window_incremental_predicates('london_date'),
        pre_hook="{{ delete_window_rows('london_date') }}",
    )
}}

-- Trains seen on every station's boards in the line feed, counted by
-- vehicle id (ADR-014, M8), with the measurement stations' rules
-- (macros/inference.sql). The station quality gate compares inferred
-- arrivals with this count. Rebuilt by window like
-- int_station_inferred_arrivals, keyed on the London date of the pass.
-- `visit_number` counts from the start of each run's read window, so only
-- `vehicle_pass_key` (which uses the first sighting) identifies a visit.

{%- set bounds = window_rebuild_bounds() %}

{{ vehicle_passes(
    predictions=ref('stg_line_arrivals'),
    predictions_where="dt between date '" ~ bounds.read_start ~ "' and date '" ~ bounds.end ~ "'",
    output_where="cast(" ~ london_local('pass_time') ~ " as date) between date '" ~ bounds.start ~ "' and date '" ~ bounds.end ~ "'",
) }}
