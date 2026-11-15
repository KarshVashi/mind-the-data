{{ config(materialized='table') }}

-- Monday Morning Index (PLAN 6.2, M6, D49): the Disruption Index over each
-- Monday's AM peak (the `am_peak` band of time_bands, 07:00 to 10:00 London
-- time), per line and ISO week. Same rules as the weekly table: missing,
-- engineering works and Service Closed minutes leave the score (PLAN 7),
-- the index is the time-weighted mean severity x 100 (D19; every minute
-- here weighs 2, so it is the plain mean), ties go to the higher Good
-- Service % (D20), and a line that ran under `min_open_share_to_rank` of
-- the peak because of works is not ranked (D22). A Monday that is a public
-- holiday is still a weekday here (D21).

with minutes as (
    select
        line_id,
        london_date,
        is_missing,
        is_engineering_works,
        is_scored,
        status_severity,
        severity_weight,
        time_weight
    from {{ ref('int_line_minute_status') }}
    where
        is_scheduled
        and day_of_week(london_date) = 1
        and time_band = 'am_peak'
),

mornings as (
    select
        line_id,
        london_date as week_start,
        count(*) as scheduled_minutes,
        count_if(not is_missing) as observed_minutes,
        count_if(is_engineering_works) as engineering_works_minutes,
        count_if(is_scored) as scored_minutes,
        count_if(is_scored and status_severity = 10) as good_service_minutes,
        coalesce(sum(if(is_scored, severity_weight * time_weight, 0)), 0)
            as weighted_disruption_minutes,
        coalesce(sum(if(is_scored, time_weight, 0)), 0) as time_weighted_scored_minutes
    from minutes
    group by line_id, london_date
),

scored as (
    select
        mornings.*,
        1e0 * mornings.observed_minutes / mornings.scheduled_minutes as completeness,
        1e0 * mornings.engineering_works_minutes / mornings.scheduled_minutes
            as engineering_works_share,
        {{ disruption_index(
            'mornings.weighted_disruption_minutes',
            'mornings.time_weighted_scored_minutes',
        ) }} as monday_morning_index,
        {{ percent_of('mornings.good_service_minutes', 'mornings.scored_minutes') }}
            as good_service_pct
    from mornings
    inner join {{ ref('dim_lines') }} as in_scope_lines
        on mornings.line_id = in_scope_lines.line_id
),

rankable as (
    select
        *,
        case
            -- Works first: a line closed all week has no scored minutes too.
            when
                engineering_works_share > 1 - {{ var('min_open_share_to_rank') }}
                then 'engineering_works'
            when monday_morning_index is null then 'no_scored_minutes'
        end as unranked_reason
    from scored
)

select
    line_id,
    week_start,
    scheduled_minutes,
    observed_minutes,
    engineering_works_minutes,
    scored_minutes,
    good_service_minutes,
    weighted_disruption_minutes,
    time_weighted_scored_minutes,
    completeness,
    engineering_works_share,
    monday_morning_index,
    good_service_pct,
    completeness < {{ var('provisional_completeness') }} as is_provisional,
    unranked_reason,
    if(
        unranked_reason is null,
        rank() over (
            partition by week_start, unranked_reason is null
            order by monday_morning_index asc, good_service_pct desc
        )
    ) as monday_morning_rank,
    {{ row_key(['line_id', 'week_start']) }} as monday_morning_key
from rankable
