{{ config(materialized='table') }}

-- Declared-view metrics per line and London date (PLAN 7.1). Only scheduled
-- minutes count. Missing minutes leave both numerator and denominator, and
-- completeness sits beside every score.

with minutes as (
    select
        line_id,
        london_date,
        week_start,
        is_missing,
        is_engineering_works,
        is_service_closed,
        is_scored,
        status_severity,
        severity_weight,
        time_weight
    from {{ ref('int_line_minute_status') }}
    where is_scheduled
),

daily as (
    select
        line_id,
        london_date,
        week_start,
        count(*) as scheduled_minutes,
        count_if(not is_missing) as observed_minutes,
        count_if(is_missing) as missing_minutes,
        count_if(is_engineering_works) as engineering_works_minutes,
        count_if(is_service_closed) as service_closed_minutes,
        count_if(is_scored) as scored_minutes,
        count_if(is_scored and status_severity = 10) as good_service_minutes,
        count_if(is_scored and severity_weight > 0) as disrupted_minutes,
        coalesce(sum(if(is_scored, severity_weight * time_weight, 0)), 0)
            as weighted_disruption_minutes
    from minutes
    group by line_id, london_date, week_start
),

incidents as (
    select
        line_id,
        start_london_date,
        recovery_minutes
    from {{ ref('int_incidents') }}
),

incident_counts as (
    select
        line_id,
        start_london_date,
        count(*) as incidents
    from incidents
    group by line_id, start_london_date
),

-- Exact median: the mean of the middle one or two recovery times.
recovery_ranked as (
    select
        line_id,
        start_london_date,
        recovery_minutes,
        row_number() over (
            partition by line_id, start_london_date order by recovery_minutes
        ) as recovery_position,
        count(*) over (partition by line_id, start_london_date) as recovered
    from incidents
    where recovery_minutes is not null
),

recovery_medians as (
    select
        line_id,
        start_london_date,
        avg(recovery_minutes) as median_recovery_minutes
    from recovery_ranked
    where recovery_position in ((recovered + 1) / 2, (recovered + 2) / 2)
    group by line_id, start_london_date
)

select
    daily.line_id,
    daily.london_date,
    daily.week_start,
    daily.scheduled_minutes,
    daily.observed_minutes,
    daily.missing_minutes,
    daily.engineering_works_minutes,
    daily.service_closed_minutes,
    daily.scored_minutes,
    daily.good_service_minutes,
    daily.disrupted_minutes,
    daily.weighted_disruption_minutes,
    coalesce(incident_counts.incidents, 0) as incidents,
    recovery_medians.median_recovery_minutes,
    1e0 * daily.observed_minutes / daily.scheduled_minutes as completeness,
    -- Weighted disruption minutes per 100 scored service hours.
    {{ per_100_hours('daily.weighted_disruption_minutes', 'daily.scored_minutes') }}
        as disruption_index,
    {{ percent_of('daily.good_service_minutes', 'daily.scored_minutes') }} as good_service_pct,
    {{ per_100_hours('coalesce(incident_counts.incidents, 0)', 'daily.scored_minutes') }}
        as incidents_per_100_hours,
    {{ row_key(['daily.line_id', 'daily.london_date']) }} as line_daily_key
from daily
left join incident_counts
    on
        daily.line_id = incident_counts.line_id
        and daily.london_date = incident_counts.start_london_date
left join recovery_medians
    on
        daily.line_id = recovery_medians.line_id
        and daily.london_date = recovery_medians.start_london_date
