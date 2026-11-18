{{ config(materialized='view', tags=['station']) }}

-- The line feed's predictions with a missing direction filled where the
-- boards show it (D65, macros/fill_missing_direction.sql), the same rules
-- as int_arrivals_directed. The every-station inference reads this instead
-- of stg_line_arrivals.

{{ fill_missing_direction(ref('stg_line_arrivals')) }}
