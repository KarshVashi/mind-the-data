{{ config(materialized='table') }}

-- Measured-view metrics (PLAN 7.3) per line and ISO week: sums of
-- fct_line_measured_daily, which uses only station-direction-hours inside
-- the quality gate (ADR-014). One row for every line-week of
-- fct_line_weekly. A line seed_measured_lines leaves out is flagged
-- `not_measured` and carries no measured numbers (D43). A line-week's
-- measured numbers are published only when its own gate passes (D51);
-- otherwise `measured_withheld_reason` says why.
--
-- Kept apart from fct_line_weekly so that a failing quality gate test
-- skips this model and leaves the declared tables built (ADR-014, "Gate
-- failures and the declared view"). The table then keeps its last good
-- build; `built_at` and `built_london_date` let the export tell which of
-- its weeks were complete when it was built and withhold the rest.

with line_weeks as (
    select
        line_id,
        week_start
    from {{ ref('fct_line_weekly') }}
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
        line_weeks.line_id,
        line_weeks.week_start,
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
            and line_weeks.line_id not in (
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
            when line_weeks.line_id in (
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
        end as measured_withheld_reason
    from line_weeks
    left join {{ ref('seed_measured_lines') }} as measured_lines
        on line_weeks.line_id = measured_lines.line_id
    left join measured_weekly
        on
            line_weeks.line_id = measured_weekly.line_id
            and line_weeks.week_start = measured_weekly.week_start
),

ranked as (
    select
        *,
        -- Too few gated hours to rank the line on the measured view (D42).
        if(
            is_measured,
            coalesce(measured_share, 0) < {{ var('min_measured_share') }}
        ) as is_measured_provisional,
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
    from scored
)

select
    line_id,
    week_start,
    is_measured,
    measurement_status,
    scheduled_measurement_hours,
    observed_measurement_hours,
    measured_hours,
    measured_share,
    observed_trains,
    observed_hours_scheduled_trains,
    trains_run_share,
    is_measured_provisional,
    excess_wait_hours,
    excess_wait_minutes,
    long_gaps,
    long_gaps_per_100_hours,
    honesty_gap_hours,
    good_service_measured_hours,
    honesty_gap_share,
    experienced_rank,
    sum_excess_wait_seconds,
    fallback_measured_hours,
    -- Measured hours judged against an estimated timetable (D33) make the
    -- line-week's measured numbers weak evidence.
    coalesce(fallback_measured_hours, 0) > 0 as is_weak_evidence,
    gate_judged_hours,
    gate_in_band_hours,
    gate_pass_share,
    measured_withheld_reason,
    measured_withheld_reason is null as is_measured_published,
    cast(current_timestamp at time zone 'UTC' as timestamp(6)) as built_at,
    -- The London date of the build: weeks and days that ended before it
    -- were complete in this table, even if a later build was skipped.
    cast(
        {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
        as date
    ) as built_london_date,
    {{ row_key(['line_id', 'week_start']) }} as line_weekly_measured_key
from ranked
