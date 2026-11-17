{{
    config(
        tags=['station'],
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='arrival_key',
        partitioned_by=['dt'],
        on_schema_change='fail',
        incremental_predicates=window_incremental_predicates('dt'),
        pre_hook="{{ delete_window_rows('dt') }}",
    )
}}

-- Train arrivals at every station on every line (M8), inferred from the
-- line feed with the same rules as the measurement stations
-- (macros/inference.sql). A board is one stop's platform within a line's
-- response, and "the next poll" is the line's next successful poll.
--
-- Rebuilt by window, not in full (macros/window_rebuild.sql): each run
-- deletes and recomputes the arrivals dated in the staging window, reading
-- one day more of polls so a train last seen before midnight is found.
-- An arrival at the end of the input stays provisional until the next run
-- recomputes its day. `dt` is the UTC date of the arrival.

{%- set bounds = window_rebuild_bounds() %}

{{ inferred_arrivals(
    predictions=ref('stg_line_arrivals'),
    polls=ref('stg_line_arrival_polls'),
    predictions_where="arrivals.dt between date '" ~ bounds.read_start ~ "' and date '" ~ bounds.end ~ "'",
    polls_where="is_ok and dt between date '" ~ bounds.read_start ~ "' and date '" ~ bounds.end ~ "'",
    output_where="cast(expected_arrival as date) between date '" ~ bounds.start ~ "' and date '" ~ bounds.end ~ "'",
) }}
