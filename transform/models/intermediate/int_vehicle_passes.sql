{{ config(materialized='table') }}

-- Trains seen on the measurement stations' arrival boards, counted by
-- vehicle id (ADR-014); the rules are in macros/inference.sql. The quality
-- gate compares inferred arrivals with this count instead of the timetable,
-- and observed trains over scheduled trains is the published "trains run"
-- share. Rebuilt in full, like int_inferred_arrivals (D41).

{{ vehicle_passes(
    predictions=ref('int_arrivals_directed'),
    predictions_where="dt >= date '" ~ var('raw_start_date') ~ "'",
) }}
