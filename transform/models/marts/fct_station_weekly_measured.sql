{{ config(materialized='table', tags=['station']) }}

-- The measured view per ISO week (London), station, line and direction
-- (M8), for every station with line_arrivals data. The station twin of
-- fct_line_weekly_measured: the same inference, quality gate (ADR-014,
-- 80% to 120% of the trains on the boards) and long-gap rule (D34), judged
-- for the station's own hours (int_station_measured_hours).
--
-- measured_status:
-- - withheld: the line-level view withholds the whole line, so no station
--   on it is shown either: lines seed_measured_lines does not measure
--   (D43, reason line_not_measured), and lines in
--   `measured_view_withheld_lines` whose boards misfile trains on a shared
--   section (D52, reason shared_section_misfiled). Vehicle-id linking
--   follows seed_vehicle_id_lines in the shared inference, as for lines.
-- - not_measured: the station-direction-week itself fails, with the
--   line-week rules in the same order: no observed hour, no hour the gate
--   could judge, more than `gate_max_out_of_band_share` of judged hours
--   out of band (D51), or fewer gated hours than `min_measured_share` of
--   the hours of service (D42).
-- - measured: otherwise.
-- Gap measures (median gap, long gaps, excess wait) are published only for
-- measured rows. Withheld rows also hide the train counts, which the
-- misfiling or the line's method problem distorts. Nothing missing is ever
-- read as good: hours without polls are not observed, and the gate is
-- judged on observed hours only.
--
-- Stations without a timetable (Elizabeth line and Overground, which TfL
-- serves no timetables for) have no scheduled trains, trains-on-boards
-- share, excess wait, long gaps or long-gap rate: a long gap is judged
-- against the scheduled gap, so with none those stay null, never 0. Their hours of service
-- are the hours with trains, so trains_per_hour describes running hours.

with hours as (
    select *
    from {{ ref('int_station_measured_hours') }}
),

hour_gaps as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        sum(headway_seconds) as sum_headway_seconds,
        sum(headway_seconds * headway_seconds) as sum_headway_squared_seconds,
        count_if(is_long_gap) as long_gaps
    from {{ ref('int_station_service_headways') }}
    group by line_id, naptan_id, direction, london_date, london_hour
),

hour_metrics as (
    select
        hours.*,
        coalesce(hour_gaps.long_gaps, 0) as long_gaps,
        -- PLAN 7.3: actual minus scheduled wait for a passenger arriving at
        -- random, in hours with a timetable and an in-service gap.
        if(
            hour_gaps.sum_headway_seconds > 0 and hours.scheduled_sum_headway_seconds > 0,
            hour_gaps.sum_headway_squared_seconds / (2 * hour_gaps.sum_headway_seconds)
            - hours.scheduled_sum_headway_squared_seconds
            / (2 * hours.scheduled_sum_headway_seconds)
        ) as excess_wait_seconds
    from hours
    left join hour_gaps
        on
            hours.line_id = hour_gaps.line_id
            and hours.naptan_id = hour_gaps.naptan_id
            and hours.direction = hour_gaps.direction
            and hours.london_date = hour_gaps.london_date
            and hours.london_hour = hour_gaps.london_hour
),

weekly as (
    select
        line_id,
        naptan_id,
        direction,
        week_start,
        count(*) as service_hours,
        count_if(is_scheduled) as scheduled_hours,
        count_if(is_observed) as observed_hours,
        sum(if(is_observed, observed_trains, 0)) as trains_observed,
        sum(if(is_observed and is_scheduled, observed_trains, 0))
            as scheduled_hours_trains_observed,
        sum(if(is_observed and is_scheduled, scheduled_departures, 0)) as scheduled_trains,
        count_if(is_gate_judged) as gate_judged_hours,
        count_if(is_gate_judged and is_in_band) as gate_in_band_hours,
        count_if(is_in_band) as measured_hours,
        count_if(is_in_band and is_scheduled) as measured_scheduled_hours,
        sum(if(is_in_band and is_scheduled, long_gaps, 0)) as long_gaps,
        -- A gated hour with no in-service gap has no excess wait.
        count_if(is_in_band and excess_wait_seconds is not null) as excess_wait_hours,
        coalesce(sum(if(is_in_band, excess_wait_seconds)), 0) as sum_excess_wait_seconds,
        count_if(is_in_band and is_fallback) as fallback_measured_hours
    from hour_metrics
    group by line_id, naptan_id, direction, week_start
),

ranked_gaps as (
    select
        line_id,
        naptan_id,
        direction,
        week_start,
        headway_seconds,
        row_number() over station_week as gap_rank,
        count(*) over (partition by line_id, naptan_id, direction, week_start) as gap_count
    from {{ ref('int_station_service_headways') }}
    where is_in_band
    window station_week as (
        partition by line_id, naptan_id, direction, week_start
        order by headway_seconds, headway_key
    )
),

medians as (
    -- The middle gap, or the mean of the two middle gaps.
    select
        line_id,
        naptan_id,
        direction,
        week_start,
        avg(headway_seconds) / 60e0 as median_gap_minutes
    from ranked_gaps
    where gap_rank in ((gap_count + 1) / 2, (gap_count + 2) / 2)
    group by line_id, naptan_id, direction, week_start
),

statuses as (
    select
        weekly.*,
        medians.median_gap_minutes,
        coalesce(measured_lines.is_measured, false) as is_line_measured,
        -- Checked in this order; null when the row is measured.
        case
            when not coalesce(measured_lines.is_measured, false) then 'line_not_measured'
            when weekly.line_id in (
                {%- for line in var('measured_view_withheld_lines') %}
                    '{{ line }}'{{ ',' if not loop.last }}
                {%- endfor %}
            ) then 'shared_section_misfiled'
            when weekly.observed_hours = 0 then 'no_observed_hours'
            when weekly.gate_judged_hours = 0 then 'gate_not_judged'
            when
                weekly.gate_in_band_hours
                < (1 - {{ var('gate_max_out_of_band_share') }}) * weekly.gate_judged_hours
                then 'gate_failed'
            when
                weekly.measured_hours < {{ var('min_measured_share') }} * weekly.service_hours
                then 'too_few_measured_hours'
        end as measured_withheld_reason
    from weekly
    left join {{ ref('seed_measured_lines') }} as measured_lines
        on weekly.line_id = measured_lines.line_id
    left join medians
        on
            weekly.line_id = medians.line_id
            and weekly.naptan_id = medians.naptan_id
            and weekly.direction = medians.direction
            and weekly.week_start = medians.week_start
),

classified as (
    select
        *,
        case
            when measured_withheld_reason in ('line_not_measured', 'shared_section_misfiled')
                then 'withheld'
            when measured_withheld_reason is not null then 'not_measured'
            else 'measured'
        end as measured_status
    from statuses
)

select
    week_start,
    naptan_id,
    line_id,
    direction,
    measured_status,
    measured_withheld_reason,
    service_hours,
    scheduled_hours,
    measured_hours,
    gate_in_band_hours,
    fallback_measured_hours,
    cast(year_of_week(week_start) as varchar)
    || '-W'
    || lpad(cast(week_of_year(week_start) as varchar), 2, '0') as iso_week,
    cast(observed_hours as double) as observed_hours,
    if(measured_status <> 'withheld', cast(trains_observed as integer)) as trains_observed,
    if(measured_status <> 'withheld' and observed_hours > 0, 1e0 * trains_observed / observed_hours)
        as trains_per_hour,
    if(measured_status = 'measured', median_gap_minutes) as median_gap_minutes,
    -- Long gaps are judged against the timetable (D34): without a gated
    -- timetabled hour there is nothing to judge, which is null, not 0.
    if(
        measured_status = 'measured' and measured_scheduled_hours > 0,
        cast(long_gaps as integer)
    ) as long_gaps,
    if(
        measured_status = 'measured' and measured_scheduled_hours > 0,
        100e0 * long_gaps / measured_scheduled_hours
    ) as long_gaps_per_100_hours,
    if(scheduled_hours > 0, cast(scheduled_trains as integer)) as scheduled_trains,
    -- Trains run against the timetable (ADR-014), withheld with the line
    -- share for lines whose boards misfile trains.
    if(
        measured_status <> 'withheld'
        and scheduled_trains > 0
        and line_id not in (
            {%- for line in var('trains_run_withheld_lines') %}
                '{{ line }}'{{ ',' if not loop.last }}
            {%- endfor %}
        ),
        1e0 * scheduled_hours_trains_observed / scheduled_trains
    ) as trains_on_boards_share,
    if(
        measured_status = 'measured' and excess_wait_hours > 0,
        sum_excess_wait_seconds / excess_wait_hours / 60e0
    ) as excess_wait_minutes,
    cast(gate_judged_hours as integer) as gate_judged_hours,
    if(gate_judged_hours > 0, 1e0 * gate_in_band_hours / gate_judged_hours) as gate_pass_share,
    {{ row_key(['naptan_id', 'line_id', 'direction', 'week_start']) }}
        as station_weekly_measured_key
from classified
