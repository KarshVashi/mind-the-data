{{ config(materialized='view') }}

-- The measurement stations' predictions with a missing direction filled
-- where the boards show it (D65, macros/fill_missing_direction.sql). The
-- inference reads this instead of stg_arrivals. A view: every lookup is
-- per day, so the reading models' `dt` bounds reach the staging table.

{{ fill_missing_direction(ref('stg_arrivals')) }}
