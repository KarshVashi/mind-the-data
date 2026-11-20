-- rpt_line_weekly_passenger_weighted joins seed_line_demand on (NUMBAT
-- line, service day type, hour) and spreads each NUMBAT line over TfL lines
-- by seed_demand_line_map. A duplicate hour would double a line's demand,
-- and shares that do not sum to 1 would make or lose passengers (D78).
with duplicate_hours as (
    select
        numbat_line,
        'duplicate hour ' || service_day_type || ' ' || cast(hour_of_day as varchar) as problem
    from {{ ref('seed_line_demand') }}
    group by numbat_line, service_day_type, hour_of_day
    having count(*) > 1
),

share_totals as (
    select
        numbat_line,
        'shares sum to ' || cast(sum(demand_share) as varchar) as problem
    from {{ ref('seed_demand_line_map') }}
    group by numbat_line
    having abs(sum(demand_share) - 1) > 1e-9
)

select
    numbat_line,
    problem
from duplicate_hours
union all
select
    numbat_line,
    problem
from share_totals
