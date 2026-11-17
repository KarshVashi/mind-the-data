{{
    config(
        materialized='table',
    )
}}

-- Train arrivals at the measurement stations, inferred from successive
-- arrival boards (PLAN 7.2); the rules are in macros/inference.sql.
--
-- Rebuilt in full each run, like the other intermediate models (ADR-012,
-- D24; DECISIONS.md D41): an arrival depends only on its poll and the
-- station's next successful poll, but an arrival at the end of the input is
-- provisional, and a merge could not remove one that a later poll turns
-- into a moving train.

{{ inferred_arrivals(
    predictions=ref('stg_arrivals'),
    polls=ref('stg_polls'),
    predictions_where="arrivals.dt >= date '" ~ var('raw_start_date') ~ "'",
    polls_where="feed = 'arrivals' and is_ok and dt >= date '" ~ var('raw_start_date') ~ "'",
) }}
