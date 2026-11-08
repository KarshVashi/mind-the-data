{{ config(materialized='table') }}

-- Unplanned disruptions per line (PLAN 6.2). An incident minute is a scored
-- minute whose worst status is unplanned with a weight above 0. Every other
-- observed minute is a quiet minute, including minutes outside service hours
-- and engineering works. Missing minutes are neither: no data can neither
-- start nor end an incident, so they are bridged. Incident minutes merge
-- into one incident while fewer than incident_merge_gap_minutes quiet
-- minutes separate them. Rebuilt in full, like int_status_intervals.

with observed as (
    select
        line_id,
        minute_utc,
        london_date,
        week_start,
        status_severity,
        status_severity_description,
        severity_weight,
        reason,
        coalesce(is_scored and not is_planned and severity_weight > 0, false)
            as is_incident_minute
    from {{ ref('int_line_minute_status') }}
    where not is_missing
),

counted as (
    select
        *,
        sum(if(is_incident_minute, 0, 1)) over (
            partition by line_id order by minute_utc
            rows between unbounded preceding and current row
        ) as quiet_minutes_so_far,
        sum(if(is_incident_minute, 0, 1)) over (partition by line_id) as quiet_minutes_total
    from observed
),

incident_minutes as (
    select
        *,
        quiet_minutes_so_far - lag(quiet_minutes_so_far) over (
            partition by line_id order by minute_utc
        ) as quiet_minutes_before
    from counted
    where is_incident_minute
),

numbered as (
    select
        *,
        sum(
            if(
                quiet_minutes_before is null
                or quiet_minutes_before >= {{ var('incident_merge_gap_minutes') }},
                1,
                0
            )
        ) over (
            partition by line_id order by minute_utc
            rows between unbounded preceding and current row
        ) as incident_number
    from incident_minutes
),

incidents as (
    select
        line_id,
        incident_number,
        min(minute_utc) as started_at,
        max(minute_utc) as last_incident_minute,
        count(*) as incident_minutes,
        min_by(london_date, minute_utc) as start_london_date,
        min_by(week_start, minute_utc) as start_week_start,
        max(severity_weight) as peak_severity_weight,
        max_by(status_severity, severity_weight * 100 - status_severity) as worst_status_severity,
        max_by(status_severity_description, severity_weight * 100 - status_severity)
            as worst_status_description,
        min_by(reason, minute_utc) as first_reason,
        -- Quiet minutes seen after the last incident minute. Fewer than the
        -- merge gap means the next minutes could still extend it.
        max(quiet_minutes_total) - max_by(quiet_minutes_so_far, minute_utc)
            as quiet_minutes_after
    from numbered
    group by line_id, incident_number
)

select
    line_id,
    started_at,
    date_add('minute', 1, last_incident_minute) as ended_at,
    start_london_date,
    start_week_start,
    incident_minutes,
    date_diff('minute', started_at, last_incident_minute) + 1 as duration_minutes,
    quiet_minutes_after < {{ var('incident_merge_gap_minutes') }} as is_open,
    -- Recovery: minutes from the first incident minute until the line left
    -- the incident for good. Unknown while the incident is open.
    if(
        quiet_minutes_after < {{ var('incident_merge_gap_minutes') }},
        null,
        date_diff('minute', started_at, last_incident_minute) + 1
    ) as recovery_minutes,
    peak_severity_weight,
    worst_status_severity,
    worst_status_description,
    first_reason,
    {{ row_key(['line_id', 'started_at']) }} as incident_key
from incidents
