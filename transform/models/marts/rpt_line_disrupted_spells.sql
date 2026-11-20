-- Exploratory (D78, ADR-020): not built in prod and read by nothing published.
{{ config(materialized='table', tags=['exploratory'], enabled=target.name != 'prod') }}

-- Disrupted scored minutes per line, week, status, reason, NUMBAT service
-- day type and London hour, for scripts/analysis/critical_links.py. The
-- service day follows NUMBAT's 05:00 to 04:59 traffic day, as in
-- rpt_line_weekly_passenger_weighted, so the London-time logic stays here.

with disrupted as (
    select
        line_id,
        week_start,
        london_date,
        status_severity_description,
        reason,
        severity_weight,
        hour(london_ts) as hour_of_day
    from {{ ref('int_line_minute_status') }}
    where is_scored and severity_weight > 0
)

select
    disrupted.line_id,
    disrupted.week_start,
    disrupted.status_severity_description as status,
    disrupted.reason,
    disrupted.severity_weight,
    day_types.day_type as service_day_type,
    disrupted.hour_of_day,
    count(*) as minutes
from disrupted
inner join {{ ref('day_types') }} as day_types
    on
        day_of_week(
            if(
                disrupted.hour_of_day < 5,
                date_add('day', -1, disrupted.london_date),
                disrupted.london_date
            )
        )
        = day_types.iso_day_of_week
group by
    disrupted.line_id,
    disrupted.week_start,
    disrupted.status_severity_description,
    disrupted.reason,
    disrupted.severity_weight,
    day_types.day_type,
    disrupted.hour_of_day
