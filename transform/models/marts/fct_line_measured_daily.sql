{{ config(materialized='table') }}

-- Measured-view sums per line and London date (PLAN 7.3), from
-- int_measured_hours. Every column is a count or a sum, so any window of
-- days adds up: fct_line_weekly_measured sums these rows for the ISO week, and the
-- publish code sums them for a custom window, both with the same rules.
--
-- Only lines seed_measured_lines measures have rows (D43). A station-
-- direction-hour counts for reliability when it passes the quality gate
-- (ADR-014). The line-week gate (D51) is judged on hours at core and branch
-- stations: termini over-count (D33), and they are already left out of the
-- M4 acceptance.

with hours as (
    select *
    from {{ ref('int_measured_hours') }}
    where is_line_measured
),

measured_line_hours as (
    -- One row per line and London hour with a measured station-direction-
    -- hour: the Honesty Gap's denominator, once joined to Good Service.
    select distinct
        line_id,
        london_date,
        london_hour
    from hours
    where counts_for_reliability
),

corroborated_directions as (
    -- D68: the hour counts toward the Honesty Gap only when at least
    -- `honesty_gap_min_stations` measurement stations, in the same
    -- direction, each had a measured hour with a long gap ending in it whose
    -- whole span was Good Service. One board dropping a train makes a gap
    -- at that station only. Directions are the line's own (inbound,
    -- outbound), so stations on different branches corroborate each other.
    select
        line_id,
        london_date,
        london_hour,
        direction
    from hours
    where counts_for_reliability and good_service_long_gaps > 0
    group by line_id, london_date, london_hour, direction
    having count(distinct naptan_id) >= {{ var('honesty_gap_min_stations') }}
),

corroborated_line_hours as (
    select distinct
        line_id,
        london_date,
        london_hour
    from corroborated_directions
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
        measured_line_hours.london_date,
        count(*) as good_service_measured_hours,
        count(corroborated_line_hours.line_id) as honesty_gap_hours
    from measured_line_hours
    inner join good_service_hours
        on
            measured_line_hours.line_id = good_service_hours.line_id
            and measured_line_hours.london_date = good_service_hours.london_date
            and measured_line_hours.london_hour = good_service_hours.london_hour
    left join corroborated_line_hours
        on
            measured_line_hours.line_id = corroborated_line_hours.line_id
            and measured_line_hours.london_date = corroborated_line_hours.london_date
            and measured_line_hours.london_hour = corroborated_line_hours.london_hour
    group by measured_line_hours.line_id, measured_line_hours.london_date
),

daily as (
    select
        line_id,
        london_date,
        week_start,
        count(*) as scheduled_measurement_hours,
        count_if(is_observed) as observed_measurement_hours,
        -- Trains run against the timetable (ADR-014): trains seen on the
        -- boards over scheduled trains, in observed hours at core and
        -- branch stations (D66). At a terminus TfL lists departing trains
        -- with no direction, or arriving ones on several platforms, so it
        -- reads far under or over the timetable; the gate leaves termini
        -- out for the same reason (D33, D51).
        sum(if(is_observed and role <> 'terminus', observed_trains, 0)) as observed_trains,
        sum(if(is_observed and role <> 'terminus', scheduled_departures, 0))
            as observed_hours_scheduled_trains,
        count_if(counts_for_reliability) as measured_hours,
        -- A gated hour with no in-service gap ending in it has no excess
        -- wait, so excess wait has its own denominator.
        count_if(counts_for_reliability and excess_wait_seconds is not null)
            as excess_wait_hours,
        coalesce(sum(if(counts_for_reliability, excess_wait_seconds)), 0)
            as sum_excess_wait_seconds,
        sum(if(counts_for_reliability, long_gaps, 0)) as long_gaps,
        -- Measured hours judged against an estimated timetable (D33).
        count_if(counts_for_reliability and is_fallback) as fallback_measured_hours,
        count_if(is_gate_judged and role <> 'terminus') as gate_judged_hours,
        count_if(is_gate_judged and role <> 'terminus' and is_in_band) as gate_in_band_hours
    from hours
    group by line_id, london_date, week_start
)

select
    daily.line_id,
    daily.london_date,
    daily.week_start,
    daily.scheduled_measurement_hours,
    daily.observed_measurement_hours,
    daily.observed_trains,
    daily.observed_hours_scheduled_trains,
    daily.measured_hours,
    daily.excess_wait_hours,
    daily.sum_excess_wait_seconds,
    daily.long_gaps,
    daily.fallback_measured_hours,
    daily.gate_judged_hours,
    daily.gate_in_band_hours,
    coalesce(honesty.good_service_measured_hours, 0) as good_service_measured_hours,
    coalesce(honesty.honesty_gap_hours, 0) as honesty_gap_hours,
    {{ row_key(['daily.line_id', 'daily.london_date']) }} as line_measured_daily_key
from daily
left join honesty
    on
        daily.line_id = honesty.line_id
        and daily.london_date = honesty.london_date
