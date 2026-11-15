{{ config(materialized='table') }}

-- The measured view per line, measurement station, direction and London
-- hour, with the PLAN 7.2 quality gate. Every scheduled station-direction-
-- hour on a day the collector ran gets a row, including hours with no
-- inferred train, so a suspension reads as 0% of the timetable rather than
-- vanishing.
--
-- Scheduled service for London date d, hour h is the timetable's service
-- hour h of d's day type plus service hour h + 24 of the previous day's
-- day type (Night Tube), each from the timetable version valid on its own
-- service day (D30, D32). The sums are additive, so both are added.
--
-- An hour is observed when the station had at least `min_polls_per_hour`
-- successful arrivals polls in it, no collection gap touches it, and it is
-- one UTC hour: the October fall-back hour 01:00 happens twice in London
-- time and would hold two hours of trains against one of timetable. The
-- gate is scored on observed hours only: a poller gap is missing data,
-- never a failed hour.
--
-- The gate tests the inference, not the service (ADR-014): inferred trains
-- against the trains observed on the boards, which are the vehicle-id
-- passes (int_vehicle_passes) plus the inferred arrivals that carry no
-- vehicle id. An hour with no train on the boards and none inferred has
-- nothing to judge; one with inferred trains but none on the boards fails. An
-- observed hour counts toward Experienced Reliability when its line is
-- measured (seed_measured_lines, D43) and the ratio is inside the band.
-- Observed over scheduled trains is reported beside it as trains run
-- against the timetable.

with stations as (
    select
        stations.line_id,
        stations.naptan_id,
        stations.role,
        '/StopPoint/' || stations.naptan_id || '/Arrivals' as request_path,
        coalesce(measured.is_measured, false) as is_line_measured
    from {{ ref('seed_measurement_stations') }} as stations
    left join {{ ref('seed_measured_lines') }} as measured
        on stations.line_id = measured.line_id
),

polls as (
    select
        request_path,
        polled_at,
        {{ london_local('polled_at') }} as london_ts,
        lead(polled_at) over (
            partition by request_path order by polled_at, poll_id
        ) as next_polled_at
    from {{ ref('stg_polls') }}
    where
        feed = 'arrivals'
        and is_ok
        and dt >= date '{{ var("raw_start_date") }}'
),

poll_hours as (
    select
        request_path,
        cast(london_ts as date) as london_date,
        hour(london_ts) as london_hour,
        count(*) as successful_polls,
        count(distinct date_trunc('hour', polled_at)) as utc_hours
    from polls
    group by request_path, cast(london_ts as date), hour(london_ts)
),

gap_hours as (
    -- Every London hour a collection gap touches, not only the one it
    -- starts in: a gap from 09:59 to 10:02 leaves both 09:00 and 10:00
    -- short of trains. least/greatest keep the range ascending across the
    -- fall-back hour.
    select distinct
        gaps.request_path,
        cast(touched.hour_start as date) as london_date,
        hour(touched.hour_start) as london_hour
    from (
        select
            request_path,
            london_ts,
            {{ london_local('next_polled_at') }} as next_london_ts
        from polls
        where
            date_diff('millisecond', polled_at, next_polled_at)
            > {{ var('collection_gap_seconds') }} * 1000
    ) as gaps
    cross join
        unnest(
            sequence(
                date_trunc('hour', least(gaps.london_ts, gaps.next_london_ts)),
                date_trunc('hour', greatest(gaps.london_ts, gaps.next_london_ts)),
                interval '1' hour
            )
        ) as touched (hour_start)
),

collected_days as (
    select distinct london_date
    from poll_hours
),

days as (
    select
        collected_days.london_date,
        today.day_type,
        previous_day.day_type as previous_day_type
    from collected_days
    inner join {{ ref('day_types') }} as today
        on day_of_week(collected_days.london_date) = today.iso_day_of_week
    inner join {{ ref('day_types') }} as previous_day
        on
            day_of_week(date_add('day', -1, collected_days.london_date))
            = previous_day.iso_day_of_week
),

scheduled_rows as (
    select
        schedule.line_id,
        schedule.naptan_id,
        schedule.direction,
        days.london_date,
        schedule.london_hour,
        schedule.scheduled_departures,
        schedule.sum_headway_seconds,
        schedule.sum_headway_squared_seconds,
        schedule.max_headway_seconds,
        schedule.is_fallback
    from {{ ref('int_scheduled_headways') }} as schedule
    inner join days
        on
            (
                not schedule.is_next_calendar_day
                and schedule.day_type = days.day_type
                and (schedule.valid_from is null or schedule.valid_from <= days.london_date)
                and (schedule.valid_to is null or schedule.valid_to > days.london_date)
            )
            or (
                schedule.is_next_calendar_day
                and schedule.day_type = days.previous_day_type
                and (
                    schedule.valid_from is null
                    or schedule.valid_from <= date_add('day', -1, days.london_date)
                )
                and (
                    schedule.valid_to is null
                    or date_add('day', -1, days.london_date) < schedule.valid_to
                )
            )
),

scheduled as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        sum(scheduled_departures) as scheduled_departures,
        sum(sum_headway_seconds) as scheduled_sum_headway_seconds,
        sum(sum_headway_squared_seconds) as scheduled_sum_headway_squared_seconds,
        max(max_headway_seconds) as scheduled_max_headway_seconds,
        bool_or(is_fallback) as is_fallback
    from scheduled_rows
    group by line_id, naptan_id, direction, london_date, london_hour
),

inferred as (
    select
        arrivals.line_id,
        arrivals.naptan_id,
        arrivals.direction,
        cast({{ london_local('arrivals.arrival_time') }} as date) as london_date,
        hour({{ london_local('arrivals.arrival_time') }}) as london_hour,
        count(*) as inferred_arrivals,
        count_if(arrivals.last_vehicle_id is null) as inferred_without_vehicle_id
    from {{ ref('int_inferred_arrivals') }} as arrivals
    where arrivals.direction is not null
    group by
        arrivals.line_id,
        arrivals.naptan_id,
        arrivals.direction,
        cast({{ london_local('arrivals.arrival_time') }} as date),
        hour({{ london_local('arrivals.arrival_time') }})
),

passes as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        count(*) as vehicle_passes
    from {{ ref('int_vehicle_passes') }}
    group by line_id, naptan_id, direction, london_date, london_hour
),

in_service_headways as (
    -- A gap counts only when every London hour it spans has scheduled
    -- service at the station and direction, so the overnight break between
    -- the last train and the first is not read as a five-hour gap (D30).
    -- Each gap is expanded into the hours it spans and equi-joined, so the
    -- join stays linear in the number of gaps.
    select
        headways.headway_key,
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.london_date,
        headways.london_hour,
        headways.headway_seconds
    from {{ ref('int_headways') }} as headways
    cross join
        unnest(
            sequence(
                date_trunc('hour', least(headways.previous_london_ts, headways.london_ts)),
                date_trunc('hour', greatest(headways.previous_london_ts, headways.london_ts)),
                interval '1' hour
            )
        ) as spanned (hour_start)
    left join scheduled
        on
            headways.line_id = scheduled.line_id
            and headways.naptan_id = scheduled.naptan_id
            and headways.direction = scheduled.direction
            and cast(spanned.hour_start as date) = scheduled.london_date
            and hour(spanned.hour_start) = scheduled.london_hour
    where not headways.spans_collection_gap
    group by
        headways.headway_key,
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.london_date,
        headways.london_hour,
        headways.headway_seconds
    having count(scheduled.line_id) = count(*)
),

headway_hours as (
    -- Long gaps are judged against the longest scheduled gap of the hour
    -- (D34), so only a gap no scheduled gap comes near counts.
    select
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.london_date,
        headways.london_hour,
        count(*) as headways,
        sum(headways.headway_seconds) as sum_headway_seconds,
        sum(headways.headway_seconds * headways.headway_seconds) as sum_headway_squared_seconds,
        max(headways.headway_seconds) as max_headway_seconds,
        count_if(
            headways.headway_seconds > 2 * scheduled.scheduled_max_headway_seconds
        ) as long_gaps
    from in_service_headways as headways
    inner join scheduled
        on
            headways.line_id = scheduled.line_id
            and headways.naptan_id = scheduled.naptan_id
            and headways.direction = scheduled.direction
            and headways.london_date = scheduled.london_date
            and headways.london_hour = scheduled.london_hour
    group by
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.london_date,
        headways.london_hour
),

hours as (
    select
        scheduled.*,
        stations.role,
        stations.request_path,
        stations.is_line_measured,
        coalesce(poll_hours.successful_polls, 0) as successful_polls,
        coalesce(poll_hours.successful_polls, 0) >= {{ var('min_polls_per_hour') }}
        and poll_hours.utc_hours = 1
        and gap_hours.request_path is null as is_observed,
        coalesce(inferred.inferred_arrivals, 0) as inferred_arrivals,
        coalesce(passes.vehicle_passes, 0) as vehicle_passes,
        coalesce(inferred.inferred_without_vehicle_id, 0) as inferred_without_vehicle_id,
        coalesce(passes.vehicle_passes, 0)
        + coalesce(inferred.inferred_without_vehicle_id, 0) as observed_trains,
        coalesce(headway_hours.headways, 0) as headways,
        headway_hours.sum_headway_seconds,
        headway_hours.sum_headway_squared_seconds,
        headway_hours.max_headway_seconds,
        coalesce(headway_hours.long_gaps, 0) as long_gaps
    from scheduled
    inner join stations
        on
            scheduled.line_id = stations.line_id
            and scheduled.naptan_id = stations.naptan_id
    left join poll_hours
        on
            stations.request_path = poll_hours.request_path
            and scheduled.london_date = poll_hours.london_date
            and scheduled.london_hour = poll_hours.london_hour
    left join gap_hours
        on
            stations.request_path = gap_hours.request_path
            and scheduled.london_date = gap_hours.london_date
            and scheduled.london_hour = gap_hours.london_hour
    left join inferred
        on
            scheduled.line_id = inferred.line_id
            and scheduled.naptan_id = inferred.naptan_id
            and scheduled.direction = inferred.direction
            and scheduled.london_date = inferred.london_date
            and scheduled.london_hour = inferred.london_hour
    left join passes
        on
            scheduled.line_id = passes.line_id
            and scheduled.naptan_id = passes.naptan_id
            and scheduled.direction = passes.direction
            and scheduled.london_date = passes.london_date
            and scheduled.london_hour = passes.london_hour
    left join headway_hours
        on
            scheduled.line_id = headway_hours.line_id
            and scheduled.naptan_id = headway_hours.naptan_id
            and scheduled.direction = headway_hours.direction
            and scheduled.london_date = headway_hours.london_date
            and scheduled.london_hour = headway_hours.london_hour
    where scheduled.scheduled_departures > 0
),

gated as (
    select
        *,
        1e0 * inferred_arrivals / scheduled_departures as arrival_ratio,
        1e0 * observed_trains / scheduled_departures as trains_run_ratio,
        if(observed_trains > 0, 1e0 * inferred_arrivals / observed_trains) as fidelity_ratio,
        -- Judged when anything was on the boards or inferred: trains inferred
        -- with none on the boards are exactly what the gate must catch.
        is_observed and (observed_trains > 0 or inferred_arrivals > 0) as is_gate_judged,
        is_observed
        and observed_trains > 0
        and 1e0 * inferred_arrivals / observed_trains
        between {{ var('gate_low_ratio') }} and {{ var('gate_high_ratio') }} as is_in_band
    from hours
)

select
    line_id,
    naptan_id,
    request_path,
    direction,
    role,
    london_date,
    london_hour,
    date_trunc('week', london_date) as week_start,
    is_line_measured,
    is_fallback,
    successful_polls,
    is_observed,
    scheduled_departures,
    inferred_arrivals,
    vehicle_passes,
    inferred_without_vehicle_id,
    observed_trains,
    arrival_ratio,
    trains_run_ratio,
    fidelity_ratio,
    is_gate_judged,
    is_in_band,
    is_line_measured and is_in_band as counts_for_reliability,
    headways,
    sum_headway_seconds,
    sum_headway_squared_seconds,
    max_headway_seconds,
    long_gaps,
    scheduled_max_headway_seconds,
    -- PLAN 7.3: average wait for a passenger arriving at random, actual
    -- from the observed gaps and scheduled from the timetable's.
    if(sum_headway_seconds > 0, sum_headway_squared_seconds / (2 * sum_headway_seconds))
        as actual_average_wait_seconds,
    if(
        scheduled_sum_headway_seconds > 0,
        scheduled_sum_headway_squared_seconds / (2 * scheduled_sum_headway_seconds)
    ) as scheduled_average_wait_seconds,
    if(
        sum_headway_seconds > 0 and scheduled_sum_headway_seconds > 0,
        sum_headway_squared_seconds / (2 * sum_headway_seconds)
        - scheduled_sum_headway_squared_seconds / (2 * scheduled_sum_headway_seconds)
    ) as excess_wait_seconds,
    {{ row_key(['line_id', 'naptan_id', 'direction', 'london_date', 'london_hour']) }}
        as measured_hour_key
from gated
