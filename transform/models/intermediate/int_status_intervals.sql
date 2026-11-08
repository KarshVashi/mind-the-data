{{ config(materialized='table') }}

-- Runs of consecutive minutes in one state per line. Rebuilt in full each
-- run: a run can start before any incremental window, so merging partial
-- runs would split or duplicate them.

with minutes as (
    select
        line_id,
        minute_utc,
        is_missing,
        status_severity,
        status_severity_description,
        is_planned,
        severity_weight,
        reason,
        is_scheduled,
        -- A missing minute is its own state, never folded into a status.
        concat_ws(
            '|',
            cast(is_missing as varchar),
            coalesce(cast(status_severity as varchar), '-'),
            coalesce(cast(is_planned as varchar), '-')
        ) as run_state
    from {{ ref('int_line_minute_status') }}
),

islands as (
    select
        *,
        row_number() over (partition by line_id order by minute_utc)
        - row_number() over (partition by line_id, run_state order by minute_utc) as island
    from minutes
)

select
    line_id,
    min(minute_utc) as interval_start_utc,
    -- Exclusive end: the minute after the run's last minute.
    date_add('minute', 1, max(minute_utc)) as interval_end_utc,
    count(*) as duration_minutes,
    count_if(is_scheduled) as scheduled_minutes,
    arbitrary(is_missing) as is_missing,
    arbitrary(status_severity) as status_severity,
    arbitrary(status_severity_description) as status_severity_description,
    arbitrary(is_planned) as is_planned,
    max(severity_weight) as severity_weight,
    -- TfL rewords reasons within one status; the run keeps its first.
    min_by(reason, minute_utc) as reason,
    {{ row_key(['line_id', 'min(minute_utc)']) }} as status_interval_key
from islands
group by line_id, run_state, island
