-- int_line_minute_status joins line_operating_hours on (line_id, day_type)
-- and expects at most one row, so a duplicate would double minutes.
select
    line_id,
    day_type,
    count(*) as hours_rows
from {{ ref('line_operating_hours') }}
group by line_id, day_type
having count(*) > 1
