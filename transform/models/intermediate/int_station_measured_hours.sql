{{ config(materialized='table') }}

-- The measured view's hours for every station (M8): one row per line,
-- station, direction and London hour of service, with the quality gate of
-- int_measured_hours (PLAN 7.2, ADR-014) applied to the line feed.
--
-- Hours of service (the spine). Where the station-direction has a timetable
-- for the day (int_scheduled_headways, now fetched for every Tube, DLR and
-- tram station), its scheduled hours, read exactly as int_measured_hours
-- reads them (Night Tube hours from the previous day, D30, D32), so a
-- suspension reads as 0 trains against the timetable. Where it has none
-- that day (Elizabeth line and Overground stations, for which TfL serves no
-- timetable, or a station whose weekly fetch has not run yet), the hours in
-- which any train was inferred or seen on the boards: with no timetable an
-- hour with no train cannot be told from an hour with no service, so such
-- hours never count as gaps. `is_scheduled` says which. Only days on which
-- the line's feed had a successful poll have hours.
--
-- An hour is observed when the line had at least `min_polls_per_hour`
-- successful line_arrivals polls in it, no collection gap touches it, and
-- it is one UTC hour (not the repeated fall-back hour). The gate compares
-- inferred trains with trains seen on the boards (vehicle-id passes plus
-- inferred arrivals without an id) and is judged on observed hours only;
-- a poller gap is missing data, never a failed hour.
--
-- Headway measures are per gap, in int_station_service_headways, which
-- uses this spine to drop gaps across hours without service.

with stations as (
    select
        line_ids.line_id,
        stations.naptan_id,
        '/Line/' || line_ids.line_id || '/Arrivals' as request_path
    from {{ ref('dim_station') }} as stations
    cross join unnest(stations.line_ids) as line_ids (line_id)
),

polls as (
    select
        request_path,
        polled_at,
        {{ london_local('polled_at') }} as london_ts,
        lead(polled_at) over (
            partition by request_path order by polled_at, poll_id
        ) as next_polled_at
    from {{ ref('stg_line_arrival_polls') }}
    where
        is_ok
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
    -- Every London hour a collection gap touches, as in int_measured_hours.
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
    select distinct
        request_path,
        london_date
    from poll_hours
),

days as (
    select
        collected_days.request_path,
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
        stations.line_id,
        stations.naptan_id,
        stations.request_path,
        schedule.direction,
        days.london_date,
        schedule.london_hour,
        schedule.scheduled_departures,
        schedule.sum_headway_seconds,
        schedule.sum_headway_squared_seconds,
        schedule.max_headway_seconds,
        schedule.is_fallback
    from {{ ref('int_scheduled_headways') }} as schedule
    inner join stations
        on
            schedule.line_id = stations.line_id
            and schedule.naptan_id = stations.naptan_id
    inner join days
        on
            stations.request_path = days.request_path
            and (
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
            )
),

scheduled as (
    select
        line_id,
        naptan_id,
        request_path,
        direction,
        london_date,
        london_hour,
        sum(scheduled_departures) as scheduled_departures,
        sum(sum_headway_seconds) as scheduled_sum_headway_seconds,
        sum(sum_headway_squared_seconds) as scheduled_sum_headway_squared_seconds,
        max(max_headway_seconds) as scheduled_max_headway_seconds,
        bool_or(is_fallback) as is_fallback
    from scheduled_rows
    group by line_id, naptan_id, request_path, direction, london_date, london_hour
),

scheduled_days as (
    select distinct
        line_id,
        naptan_id,
        direction,
        london_date
    from scheduled
    where scheduled_departures > 0
),

inferred as (
    select
        line_id,
        naptan_id,
        direction,
        cast({{ london_local('arrival_time') }} as date) as london_date,
        hour({{ london_local('arrival_time') }}) as london_hour,
        count(*) as inferred_arrivals,
        count_if(last_vehicle_id is null) as inferred_without_vehicle_id
    from {{ ref('int_station_inferred_arrivals') }}
    where direction is not null
    group by
        line_id,
        naptan_id,
        direction,
        cast({{ london_local('arrival_time') }} as date),
        hour({{ london_local('arrival_time') }})
),

passes as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        count(*) as vehicle_passes
    from {{ ref('int_station_vehicle_passes') }}
    group by line_id, naptan_id, direction, london_date, london_hour
),

train_hours as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour
    from inferred
    union
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour
    from passes
),

data_hours as (
    -- Hours with any train, for station-direction-days without a timetable.
    select
        trains.line_id,
        trains.naptan_id,
        stations.request_path,
        trains.direction,
        trains.london_date,
        trains.london_hour
    from train_hours as trains
    inner join stations
        on
            trains.line_id = stations.line_id
            and trains.naptan_id = stations.naptan_id
    inner join collected_days
        on
            stations.request_path = collected_days.request_path
            and trains.london_date = collected_days.london_date
    left join scheduled_days
        on
            trains.line_id = scheduled_days.line_id
            and trains.naptan_id = scheduled_days.naptan_id
            and trains.direction = scheduled_days.direction
            and trains.london_date = scheduled_days.london_date
    where scheduled_days.line_id is null
),

spine as (
    select
        line_id,
        naptan_id,
        request_path,
        direction,
        london_date,
        london_hour,
        true as is_scheduled,
        scheduled_departures,
        scheduled_sum_headway_seconds,
        scheduled_sum_headway_squared_seconds,
        scheduled_max_headway_seconds,
        is_fallback
    from scheduled
    where scheduled_departures > 0
    union all
    select
        line_id,
        naptan_id,
        request_path,
        direction,
        london_date,
        london_hour,
        false as is_scheduled,
        cast(null as bigint) as scheduled_departures,
        cast(null as double) as scheduled_sum_headway_seconds,
        cast(null as double) as scheduled_sum_headway_squared_seconds,
        cast(null as double) as scheduled_max_headway_seconds,
        cast(null as boolean) as is_fallback
    from data_hours
),

hours as (
    select
        spine.*,
        coalesce(poll_hours.successful_polls, 0) as successful_polls,
        coalesce(poll_hours.successful_polls, 0) >= {{ var('min_polls_per_hour') }}
        and poll_hours.utc_hours = 1
        and gap_hours.request_path is null as is_observed,
        coalesce(inferred.inferred_arrivals, 0) as inferred_arrivals,
        coalesce(passes.vehicle_passes, 0) as vehicle_passes,
        coalesce(inferred.inferred_without_vehicle_id, 0) as inferred_without_vehicle_id,
        coalesce(passes.vehicle_passes, 0)
        + coalesce(inferred.inferred_without_vehicle_id, 0) as observed_trains
    from spine
    left join poll_hours
        on
            spine.request_path = poll_hours.request_path
            and spine.london_date = poll_hours.london_date
            and spine.london_hour = poll_hours.london_hour
    left join gap_hours
        on
            spine.request_path = gap_hours.request_path
            and spine.london_date = gap_hours.london_date
            and spine.london_hour = gap_hours.london_hour
    left join inferred
        on
            spine.line_id = inferred.line_id
            and spine.naptan_id = inferred.naptan_id
            and spine.direction = inferred.direction
            and spine.london_date = inferred.london_date
            and spine.london_hour = inferred.london_hour
    left join passes
        on
            spine.line_id = passes.line_id
            and spine.naptan_id = passes.naptan_id
            and spine.direction = passes.direction
            and spine.london_date = passes.london_date
            and spine.london_hour = passes.london_hour
)

select
    line_id,
    naptan_id,
    request_path,
    direction,
    london_date,
    london_hour,
    date_trunc('week', london_date) as week_start,
    is_scheduled,
    is_fallback,
    successful_polls,
    is_observed,
    scheduled_departures,
    scheduled_sum_headway_seconds,
    scheduled_sum_headway_squared_seconds,
    scheduled_max_headway_seconds,
    inferred_arrivals,
    vehicle_passes,
    inferred_without_vehicle_id,
    observed_trains,
    if(observed_trains > 0, 1e0 * inferred_arrivals / observed_trains) as fidelity_ratio,
    -- Judged when anything was on the boards or inferred (int_measured_hours).
    is_observed and (observed_trains > 0 or inferred_arrivals > 0) as is_gate_judged,
    is_observed
    and observed_trains > 0
    and 1e0 * inferred_arrivals / observed_trains
    between {{ var('gate_low_ratio') }} and {{ var('gate_high_ratio') }} as is_in_band,
    -- PLAN 7.3: expected wait for a passenger arriving at random.
    if(
        scheduled_sum_headway_seconds > 0,
        scheduled_sum_headway_squared_seconds / (2 * scheduled_sum_headway_seconds)
    ) as scheduled_average_wait_seconds,
    {{ row_key(['line_id', 'naptan_id', 'direction', 'london_date', 'london_hour']) }}
        as station_measured_hour_key
from hours
