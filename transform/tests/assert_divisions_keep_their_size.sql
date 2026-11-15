-- Promotion and relegation swap equal numbers of lines (D57), so every
-- month's Division One holds `division_one_size` lines (or every line, if
-- there are fewer), this month and next. Returns the months that break it.

select
    month_start,
    count(*) as line_count,
    count_if(division = 1) as division_one,
    count_if(next_division = 1) as next_division_one
from {{ ref('rpt_divisions_monthly') }}
group by month_start
having
    count_if(division = 1) != least({{ var('division_one_size') }}, count(*))
    or count_if(next_division = 1) != least({{ var('division_one_size') }}, count(*))
