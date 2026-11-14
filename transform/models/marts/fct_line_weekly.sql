{{ config(materialized='table') }}

-- Declared-view metrics per line and ISO week (Monday to Sunday, London
-- time), with the week's rank and the movement since the previous week.
-- Ratios are recomputed from summed minutes, never averaged across days.
-- A line that ran under `min_open_share_to_rank` of its scheduled minutes
-- because of engineering works keeps its index but is not ranked (D22).
--
-- Measured-view metrics (PLAN 7.3) are sums of fct_line_measured_daily,
-- which uses only station-direction-hours inside the quality gate
-- (ADR-014). A line seed_measured_lines leaves out is flagged `not_measured`
-- and carries no measured numbers (D43). A line-week's measured numbers are
-- published only when its own gate passes (D51); otherwise
-- `measured_withheld_reason` says why.

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

measured_weekly as (
    -- Sums of fct_line_measured_daily, so a custom window summed by the
    -- publish code gets the same numbers. Each station-direction-hour is one
    -- hour of service at one platform pair, so a plain mean over hours is
    -- the mean weighted by service hours (D42).
    select
        line_id,
        week_start,
        sum(scheduled_measurement_hours) as scheduled_measurement_hours,
        sum(observed_measurement_hours) as observed_measurement_hours,
        sum(observed_trains) as observed_trains,
        sum(observed_hours_scheduled_trains) as observed_hours_scheduled_trains,
        sum(measured_hours) as measured_hours,
        sum(excess_wait_hours) as excess_wait_hours,
        sum(sum_excess_wait_seconds) as sum_excess_wait_seconds,
        sum(long_gaps) as long_gaps,
        sum(fallback_measured_hours) as fallback_measured_hours,
        sum(gate_judged_hours) as gate_judged_hours,
        sum(gate_in_band_hours) as gate_in_band_hours,
        sum(good_service_measured_hours) as good_service_measured_hours,
        sum(honesty_gap_hours) as honesty_gap_hours
    from {{ ref('fct_line_measured_daily') }}
    group by line_id, week_start
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
        if(measured_lines.is_measured, measured_weekly.observed_trains) as observed_trains,
        if(measured_lines.is_measured, measured_weekly.observed_hours_scheduled_trains)
            as observed_hours_scheduled_trains,
        -- Circle and Hammersmith & City share boards that file one line's
        -- trains under the other, so their share is withheld (ADR-014).
        if(
            measured_lines.is_measured
            and measured_weekly.observed_hours_scheduled_trains > 0
            and weekly.line_id not in (
                {%- for line in var('trains_run_withheld_lines') %}
                    '{{ line }}'{{ ',' if not loop.last }}
                {%- endfor %}
            ),
            1e0 * measured_weekly.observed_trains / measured_weekly.observed_hours_scheduled_trains
        ) as trains_run_share,
        measured_weekly.excess_wait_hours,
        measured_weekly.sum_excess_wait_seconds,
        if(
            measured_lines.is_measured and measured_weekly.scheduled_measurement_hours > 0,
            1e0 * measured_weekly.measured_hours / measured_weekly.scheduled_measurement_hours
        ) as measured_share,
        if(
            measured_lines.is_measured and measured_weekly.excess_wait_hours > 0,
            measured_weekly.sum_excess_wait_seconds / measured_weekly.excess_wait_hours / 60e0
        ) as excess_wait_minutes,
        if(measured_lines.is_measured, measured_weekly.long_gaps) as long_gaps,
        if(
            measured_lines.is_measured and measured_weekly.measured_hours > 0,
            100e0 * measured_weekly.long_gaps / measured_weekly.measured_hours
        ) as long_gaps_per_100_hours,
        if(measured_lines.is_measured, measured_weekly.honesty_gap_hours) as honesty_gap_hours,
        if(measured_lines.is_measured, measured_weekly.good_service_measured_hours)
            as good_service_measured_hours,
        if(
            measured_lines.is_measured and measured_weekly.good_service_measured_hours > 0,
            1e0 * measured_weekly.honesty_gap_hours / measured_weekly.good_service_measured_hours
        ) as honesty_gap_share,
        measured_weekly.fallback_measured_hours,
        measured_weekly.gate_judged_hours,
        measured_weekly.gate_in_band_hours,
        if(
            measured_lines.is_measured and measured_weekly.gate_judged_hours > 0,
            1e0 * measured_weekly.gate_in_band_hours / measured_weekly.gate_judged_hours
        ) as gate_pass_share,
        case
            when not coalesce(measured_lines.is_measured, false) then 'not_measured'
            when coalesce(measured_weekly.measured_hours, 0) = 0 then 'no_measured_hours'
            else 'measured'
        end as measurement_status,
        -- Why the line-week's measured numbers are not published, checked in
        -- this order (D43, D52, D51, D42); null when they are.
        case
            when not coalesce(measured_lines.is_measured, false) then 'line_not_measured'
            when weekly.line_id in (
                {%- for line in var('measured_view_withheld_lines') %}
                    '{{ line }}'{{ ',' if not loop.last }}
                {%- endfor %}
            ) then 'shared_section_misfiled'
            when coalesce(measured_weekly.measured_hours, 0) = 0 then 'no_measured_hours'
            when coalesce(measured_weekly.gate_judged_hours, 0) = 0 then 'gate_not_judged'
            when
                measured_weekly.gate_in_band_hours
                < (1 - {{ var('gate_max_out_of_band_share') }}) * measured_weekly.gate_judged_hours
                then 'gate_failed'
            when
                measured_weekly.measured_hours
                < {{ var('min_measured_share') }} * measured_weekly.scheduled_measurement_hours
                then 'too_few_measured_hours'
        end as measured_withheld_reason,
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
    left join {{ ref('seed_measured_lines') }} as measured_lines
        on weekly.line_id = measured_lines.line_id
    left join measured_weekly
        on
            weekly.line_id = measured_weekly.line_id
            and weekly.week_start = measured_weekly.week_start
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
        -- Too few gated hours to rank the line on the measured view (D42).
        if(
            is_measured,
            coalesce(measured_share, 0) < {{ var('min_measured_share') }}
        ) as is_measured_provisional,
        -- Lowest Disruption Index first; equal indexes are split by the
        -- higher Good Service %, and still-equal lines share a rank.
        if(
            unranked_reason is null,
            rank() over (
                partition by week_start, unranked_reason is null
                order by disruption_index asc nulls last, good_service_pct desc nulls last
            )
        ) as disruption_rank,
        -- PLAN 7.3: lowest excess wait first, ties to fewer long gaps, among
        -- the line-weeks whose measured numbers are published (D51) and
        -- judged against real timetables only (D53).
        if(
            excess_wait_minutes is null
            or measured_withheld_reason is not null
            or coalesce(fallback_measured_hours, 0) > 0,
            null,
            rank() over (
                partition by
                    week_start,
                    excess_wait_minutes is null
                    or measured_withheld_reason is not null
                    or coalesce(fallback_measured_hours, 0) > 0
                order by excess_wait_minutes asc nulls last, long_gaps_per_100_hours asc nulls last
            )
        ) as experienced_rank
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
    ranked.is_measured,
    ranked.measurement_status,
    ranked.scheduled_measurement_hours,
    ranked.observed_measurement_hours,
    ranked.measured_hours,
    ranked.measured_share,
    ranked.observed_trains,
    ranked.observed_hours_scheduled_trains,
    ranked.trains_run_share,
    ranked.is_measured_provisional,
    ranked.excess_wait_hours,
    ranked.excess_wait_minutes,
    ranked.long_gaps,
    ranked.long_gaps_per_100_hours,
    ranked.honesty_gap_hours,
    ranked.good_service_measured_hours,
    ranked.honesty_gap_share,
    ranked.experienced_rank,
    ranked.sum_excess_wait_seconds,
    ranked.fallback_measured_hours,
    -- Measured hours judged against an estimated timetable (D33) make the
    -- line-week's measured numbers weak evidence.
    coalesce(ranked.fallback_measured_hours, 0) > 0 as is_weak_evidence,
    ranked.gate_judged_hours,
    ranked.gate_in_band_hours,
    ranked.gate_pass_share,
    ranked.measured_withheld_reason,
    ranked.measured_withheld_reason is null as is_measured_published,
    {{ row_key(['ranked.line_id', 'ranked.week_start']) }} as line_weekly_key
from ranked
left join ranked as previous_week
    on
        ranked.line_id = previous_week.line_id
        and previous_week.week_start = date_add('day', -7, ranked.week_start)
