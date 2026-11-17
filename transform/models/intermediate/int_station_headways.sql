{{ config(materialized='table', tags=['station']) }}

-- Gaps between consecutive inferred arrivals per line, station and
-- direction for every station (M8), flagging gaps that span a gap in the
-- line feed's polls (D28); the rules are in macros/inference.sql. The line
-- feed lists only its own line, so no station filter is needed.
--
-- Rebuilt in full: inferred arrivals are a small table (a few million rows
-- a month), unlike the predictions they come from.

{{ headways(
    arrivals=ref('int_station_inferred_arrivals'),
    polls=ref('stg_line_arrival_polls'),
    polls_where="is_ok and dt >= date '" ~ var('raw_start_date') ~ "'",
) }}
