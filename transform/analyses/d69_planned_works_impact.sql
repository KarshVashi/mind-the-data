-- D69 audit: what moves when planned works under a suspension or delay code
-- become engineering works. Run against int_line_minute_status as built
-- before D69 (main), for one ISO week:
--   dbt compile --select d69_planned_works_impact --vars '{d69_week: "2026-11-09"}'
-- then run the compiled SQL with scripts/athena_query.py --file.
-- It reclassifies only each minute's winning entry, so a minute where planned
-- works beat a smaller unplanned delay is counted as moved in full: an upper
-- bound. A dev rebuild from the branch gives the exact figures.

with patterns as (
    select
        signal,
        pattern
    from {{ ref('planned_works_reasons') }}
),

minutes as (
    select
        minutes.line_minute_key,
        minutes.line_id,
        minutes.is_scored,
        minutes.status_severity,
        minutes.severity_weight,
        minutes.time_weight,
        minutes.is_planned,
        -- Suspended, Part Suspended, Severe Delays (seed is_works_when_planned).
        minutes.status_severity in (2, 3, 6) as is_works_code,
        lower(coalesce(minutes.reason, '')) as reason_lower
    from {{ ref('int_line_minute_status') }} as minutes
    where
        minutes.week_start = date '{{ var("d69_week", "2026-11-09") }}'
        and minutes.is_scheduled
),

signals as (
    select
        minutes.line_minute_key,
        minutes.line_id,
        minutes.is_scored,
        minutes.status_severity,
        minutes.severity_weight,
        minutes.time_weight,
        minutes.is_works_code,
        minutes.is_planned,
        minutes.reason_lower,
        coalesce(bool_or(patterns.signal = 'planned_works'), false) as says_planned_works,
        coalesce(bool_or(patterns.signal = 'overrun'), false) as says_overrun
    from minutes
    left join patterns
        on regexp_like(minutes.reason_lower, patterns.pattern)
    group by
        minutes.line_minute_key,
        minutes.line_id,
        minutes.is_scored,
        minutes.status_severity,
        minutes.severity_weight,
        minutes.time_weight,
        minutes.is_works_code,
        minutes.is_planned,
        minutes.reason_lower
),

classified as (
    select
        line_id,
        is_scored,
        status_severity,
        severity_weight,
        time_weight,
        is_scored
        and is_works_code
        and not says_overrun
        and (is_planned or says_planned_works) as moves,
        is_scored
        and is_works_code
        and not says_overrun
        and not is_planned
        and says_planned_works as moves_by_text,
        is_scored and is_works_code and says_overrun as is_overrun
    from signals
),

per_line as (
    select
        line_id,
        count_if(is_scored) as scored_minutes_before,
        count_if(moves) as moved_minutes,
        count_if(moves_by_text) as moved_by_text_minutes,
        count_if(is_overrun) as overrun_minutes_kept,
        100 * sum(if(is_scored, severity_weight * time_weight, 0))
        / nullif(sum(if(is_scored, time_weight, 0)), 0) as index_before,
        100 * sum(if(is_scored and not moves, severity_weight * time_weight, 0))
        / nullif(sum(if(is_scored and not moves, time_weight, 0)), 0) as index_after,
        100e0 * count_if(is_scored and status_severity = 10)
        / nullif(count_if(is_scored), 0) as good_service_pct_before,
        100e0 * count_if(is_scored and not moves and status_severity = 10)
        / nullif(count_if(is_scored and not moves), 0) as good_service_pct_after
    from classified
    group by line_id
)

select
    line_id,
    scored_minutes_before,
    moved_minutes,
    moved_by_text_minutes,
    overrun_minutes_kept,
    round(index_before, 1) as index_before,
    round(index_after, 1) as index_after,
    round(good_service_pct_before, 1) as good_service_pct_before,
    round(good_service_pct_after, 1) as good_service_pct_after,
    rank() over (order by index_before asc, good_service_pct_before desc) as rank_before,
    rank() over (order by index_after asc, good_service_pct_after desc) as rank_after
from per_line
order by moved_minutes desc, line_id asc
