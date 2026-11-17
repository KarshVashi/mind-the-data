{{ config(materialized='table') }}

-- Gaps between consecutive inferred arrivals per line, measurement station
-- and direction (PLAN 6.2, 7.3), flagging gaps that span a collection gap
-- (D28); the rules are in macros/inference.sql. Only the lines each station
-- is measured for.

{{ headways(
    arrivals=ref('int_inferred_arrivals'),
    polls=ref('stg_polls'),
    polls_where="feed = 'arrivals' and is_ok and dt >= date '" ~ var('raw_start_date') ~ "'",
    stations=ref('seed_measurement_stations'),
) }}
