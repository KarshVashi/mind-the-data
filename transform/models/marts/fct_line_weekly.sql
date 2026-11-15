{{ config(materialized='table') }}

-- Declared-view metrics per line and ISO week (Monday to Sunday, London
-- time), with the week's rank and the movement since the previous week.
-- Ratios are recomputed from summed minutes, never averaged across days.
-- A line that ran under `min_open_share_to_rank` of its scheduled minutes
-- because of engineering works keeps its index but is not ranked (D22).
--
-- The measured view is in fct_line_weekly_measured, which reads this
-- model. Nothing here depends on int_measured_hours, so a failing quality
-- gate test (assert_measured_hours_pass_the_gate) skips only the measured
-- models and never this table or the declared reports built from it
-- (ADR-014, "Gate failures and the declared view").

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
        sum(time_weighted_scored_minutes) as time_weighted_scored_minutes,
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

scored as (
    select
        weekly.*,
        in_scope_lines.is_low_volume,
        recovery_medians.median_recovery_minutes,
        1e0 * weekly.observed_minutes / weekly.scheduled_minutes as completeness,
        {{ disruption_index(
            'weekly.weighted_disruption_minutes',
            'weekly.time_weighted_scored_minutes',
        ) }} as disruption_index,
        if(
            weekly.scheduled_minutes > 0,
            1e0 * weekly.engineering_works_minutes / weekly.scheduled_minutes
        ) as engineering_works_share,
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
),

rankable as (
    select
        *,
        -- D22 (decided 2026-11-14): a line ranks only if it ran at least
        -- `min_open_share_to_rank` of its scheduled minutes, i.e. engineering
        -- works took no more than the rest.
        case
            -- Works first: a line closed all week has no scored minutes too.
            when
                engineering_works_share > 1 - {{ var('min_open_share_to_rank') }}
                then 'engineering_works'
            when disruption_index is null then 'no_scored_minutes'
        end as unranked_reason
    from scored
),

ranked as (
    select
        *,
        unranked_reason is null as is_ranked,
        completeness < {{ var('provisional_completeness') }} as is_provisional,
        -- Lowest Disruption Index first; equal indexes are split by the
        -- higher Good Service %, and still-equal lines share a rank.
        if(
            unranked_reason is null,
            rank() over (
                partition by week_start, unranked_reason is null
                order by disruption_index asc nulls last, good_service_pct desc nulls last
            )
        ) as disruption_rank
    from rankable
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
    ranked.time_weighted_scored_minutes,
    ranked.incidents,
    ranked.median_recovery_minutes,
    ranked.completeness,
    ranked.disruption_index,
    ranked.good_service_pct,
    ranked.incidents_per_100_hours,
    ranked.is_week_complete,
    ranked.is_provisional,
    ranked.is_low_volume,
    ranked.engineering_works_share,
    ranked.is_ranked,
    ranked.unranked_reason,
    ranked.disruption_rank,
    previous_week.disruption_rank as previous_disruption_rank,
    -- Positive means the line climbed the table.
    previous_week.disruption_rank - ranked.disruption_rank as rank_movement,
    previous_week.disruption_index as previous_disruption_index,
    -- When this table was built; the export compares it with
    -- fct_line_weekly_measured.built_at to spot a measured view left
    -- behind by a failed gate.
    cast(current_timestamp at time zone 'UTC' as timestamp(6)) as built_at,
    {{ row_key(['ranked.line_id', 'ranked.week_start']) }} as line_weekly_key
from ranked
left join ranked as previous_week
    on
        ranked.line_id = previous_week.line_id
        and previous_week.week_start = date_add('day', -7, ranked.week_start)
