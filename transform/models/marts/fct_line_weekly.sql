{{ config(materialized='table') }}

-- Declared-view metrics per line and ISO week (Monday to Sunday, London
-- time), with the week's rank and the movement since the previous week.
-- Ratios are recomputed from summed minutes, never averaged across days.
--
-- Measured-view metrics (PLAN 7.3) come from int_measured_hours, using only
-- station-direction-hours that pass the quality gate on measured lines
-- (PLAN 7.2). A line seed_measured_lines leaves out is flagged
-- `not_measured` and carries no measured numbers (D42).

with weekly as (
    select
        line_id,
        week_start,
        count(*) as service_days,
        sum(scheduled_minutes) as scheduled_minutes,
        sum(observed_minutes) as observed_minutes,
        sum(missing_minutes) as missing_minutes,
        sum(engineering_works_minutes) as engineering_works_minutes,
        sum(service_closed_minutes) as service_closed_minutes,
        sum(scored_minutes) as scored_minutes,
        sum(good_service_minutes) as good_service_minutes,
        sum(disrupted_minutes) as disrupted_minutes,
        sum(weighted_disruption_minutes) as weighted_disruption_minutes,
        sum(incidents) as incidents
    from {{ ref('fct_line_daily') }}
    group by line_id, week_start
),

recovery_ranked as (
    select
        line_id,
        start_week_start,
        recovery_minutes,
        row_number() over (
            partition by line_id, start_week_start order by recovery_minutes
        ) as recovery_position,
        count(*) over (partition by line_id, start_week_start) as recovered
    from {{ ref('int_incidents') }}
    where recovery_minutes is not null
),

recovery_medians as (
    select
        line_id,
        start_week_start,
        avg(recovery_minutes) as median_recovery_minutes
    from recovery_ranked
    where recovery_position in ((recovered + 1) / 2, (recovered + 2) / 2)
    group by line_id, start_week_start
),

measured_weekly as (
    -- Each station-direction-hour is one hour of service at one platform
    -- pair, so a plain mean over hours is the mean weighted by service
    -- hours (D41).
    select
        line_id,
        week_start,
        count(*) as scheduled_measurement_hours,
        count_if(is_observed) as observed_measurement_hours,
        count_if(counts_for_reliability) as measured_hours,
        -- A gated hour with no in-service gap ending in it has no excess
        -- wait, so excess wait has its own denominator.
        count_if(counts_for_reliability and excess_wait_seconds is not null) as excess_wait_hours,
        avg(if(counts_for_reliability, excess_wait_seconds)) / 60 as excess_wait_minutes,
        sum(if(counts_for_reliability, long_gaps, 0)) as long_gaps
    from {{ ref('int_measured_hours') }}
    where is_line_measured
    group by line_id, week_start
),

measured_line_hours as (
    select
        line_id,
        week_start,
        london_date,
        london_hour,
        sum(long_gaps) as long_gaps
    from {{ ref('int_measured_hours') }}
    where counts_for_reliability
    group by line_id, week_start, london_date, london_hour
),

good_service_hours as (
    -- PLAN 7.3 Honesty Gap: the worst declared status was Good Service for
    -- the whole hour. A missing minute breaks the hour.
    select
        line_id,
        london_date,
        hour(london_ts) as london_hour
    from {{ ref('int_line_minute_status') }}
    group by line_id, london_date, hour(london_ts)
    having count(*) = count_if(not is_missing and status_severity = 10)
),

honesty as (
    select
        measured_line_hours.line_id,
        measured_line_hours.week_start,
        count(*) as good_service_measured_hours,
        count_if(measured_line_hours.long_gaps > 0) as honesty_gap_hours
    from measured_line_hours
    inner join good_service_hours
        on
            measured_line_hours.line_id = good_service_hours.line_id
            and measured_line_hours.london_date = good_service_hours.london_date
            and measured_line_hours.london_hour = good_service_hours.london_hour
    group by measured_line_hours.line_id, measured_line_hours.week_start
),

scored as (
    select
        weekly.*,
        in_scope_lines.is_low_volume,
        recovery_medians.median_recovery_minutes,
        coalesce(measured_lines.is_measured, false) as is_measured,
        measured_weekly.scheduled_measurement_hours,
        measured_weekly.observed_measurement_hours,
        measured_weekly.measured_hours,
        measured_weekly.excess_wait_hours,
        if(
            measured_lines.is_measured and measured_weekly.scheduled_measurement_hours > 0,
            1e0 * measured_weekly.measured_hours / measured_weekly.scheduled_measurement_hours
        ) as measured_share,
        if(measured_lines.is_measured, measured_weekly.excess_wait_minutes) as excess_wait_minutes,
        if(measured_lines.is_measured, measured_weekly.long_gaps) as long_gaps,
        if(
            measured_lines.is_measured and measured_weekly.measured_hours > 0,
            100e0 * measured_weekly.long_gaps / measured_weekly.measured_hours
        ) as long_gaps_per_100_hours,
        if(measured_lines.is_measured, honesty.honesty_gap_hours) as honesty_gap_hours,
        if(measured_lines.is_measured, honesty.good_service_measured_hours)
            as good_service_measured_hours,
        if(
            measured_lines.is_measured and honesty.good_service_measured_hours > 0,
            1e0 * honesty.honesty_gap_hours / honesty.good_service_measured_hours
        ) as honesty_gap_share,
        case
            when not coalesce(measured_lines.is_measured, false) then 'not_measured'
            when coalesce(measured_weekly.measured_hours, 0) = 0 then 'no_measured_hours'
            else 'measured'
        end as measurement_status,
        1e0 * weekly.observed_minutes / weekly.scheduled_minutes as completeness,
        {{ per_100_hours('weekly.weighted_disruption_minutes', 'weekly.scored_minutes') }}
            as disruption_index,
        {{ percent_of('weekly.good_service_minutes', 'weekly.scored_minutes') }}
            as good_service_pct,
        {{ per_100_hours('weekly.incidents', 'weekly.scored_minutes') }} as incidents_per_100_hours,
        -- A week is complete once its last London minute is in the data.
        date_add('day', 7, weekly.week_start)
        <= cast(
            {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
            as date
        )
            as is_week_complete
    from weekly
    inner join {{ ref('dim_lines') }} as in_scope_lines
        on weekly.line_id = in_scope_lines.line_id
    left join recovery_medians
        on
            weekly.line_id = recovery_medians.line_id
            and weekly.week_start = recovery_medians.start_week_start
    left join {{ ref('seed_measured_lines') }} as measured_lines
        on weekly.line_id = measured_lines.line_id
    left join measured_weekly
        on
            weekly.line_id = measured_weekly.line_id
            and weekly.week_start = measured_weekly.week_start
    left join honesty
        on
            weekly.line_id = honesty.line_id
            and weekly.week_start = honesty.week_start
),

ranked as (
    select
        *,
        completeness < {{ var('provisional_completeness') }} as is_provisional,
        -- Too few gated hours to rank the line on the measured view (D41).
        coalesce(measured_share, 0) < {{ var('min_measured_share') }} as is_measured_provisional,
        -- Lowest Disruption Index first; equal indexes are split by the
        -- higher Good Service %, and still-equal lines share a rank.
        if(
            disruption_index is null,
            null,
            rank() over (
                partition by week_start
                order by disruption_index asc nulls last, good_service_pct desc nulls last
            )
        ) as disruption_rank,
        -- PLAN 7.3: lowest excess wait first, ties to fewer long gaps.
        if(
            excess_wait_minutes is null
            or coalesce(measured_share, 0) < {{ var('min_measured_share') }},
            null,
            rank() over (
                partition by
                    week_start,
                    excess_wait_minutes is null
                    or coalesce(measured_share, 0) < {{ var('min_measured_share') }}
                order by excess_wait_minutes asc nulls last, long_gaps_per_100_hours asc nulls last
            )
        ) as experienced_rank
    from scored
)

select
    ranked.line_id,
    ranked.week_start,
    date_add('day', 6, ranked.week_start) as week_end,
    ranked.service_days,
    ranked.scheduled_minutes,
    ranked.observed_minutes,
    ranked.missing_minutes,
    ranked.engineering_works_minutes,
    ranked.service_closed_minutes,
    ranked.scored_minutes,
    ranked.good_service_minutes,
    ranked.disrupted_minutes,
    ranked.weighted_disruption_minutes,
    ranked.incidents,
    ranked.median_recovery_minutes,
    ranked.completeness,
    ranked.disruption_index,
    ranked.good_service_pct,
    ranked.incidents_per_100_hours,
    ranked.is_week_complete,
    ranked.is_provisional,
    ranked.is_low_volume,
    ranked.disruption_rank,
    previous_week.disruption_rank as previous_disruption_rank,
    -- Positive means the line climbed the table.
    previous_week.disruption_rank - ranked.disruption_rank as rank_movement,
    ranked.is_measured,
    ranked.measurement_status,
    ranked.scheduled_measurement_hours,
    ranked.observed_measurement_hours,
    ranked.measured_hours,
    ranked.measured_share,
    ranked.is_measured_provisional,
    ranked.excess_wait_hours,
    ranked.excess_wait_minutes,
    ranked.long_gaps,
    ranked.long_gaps_per_100_hours,
    ranked.honesty_gap_hours,
    ranked.good_service_measured_hours,
    ranked.honesty_gap_share,
    ranked.experienced_rank,
    {{ row_key(['ranked.line_id', 'ranked.week_start']) }} as line_weekly_key
from ranked
left join ranked as previous_week
    on
        ranked.line_id = previous_week.line_id
        and previous_week.week_start = date_add('day', -7, ranked.week_start)
