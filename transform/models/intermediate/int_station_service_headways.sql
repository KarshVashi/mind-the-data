{{ config(materialized='table') }}

-- The gaps the station measured view uses (M8): gaps from
-- int_station_headways that span no collection gap (D28) and whose every
-- London hour is an hour of service in int_station_measured_hours, so the
-- overnight break, or a stretch of hours with no train at a station with
-- no timetable, is never read as one long gap (D30). Each gap carries the
-- hour of the arrival that ends it, where it counts.
--
-- A gap is long when it is more than twice the longest scheduled gap of
-- that hour (D34). Without a timetable there is no scheduled gap, so no gap
-- is long or short: is_long_gap is null.

with spanned as (
    select
        headways.headway_key,
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.london_date,
        headways.london_hour,
        headways.previous_arrival_time,
        headways.arrival_time,
        headways.headway_seconds,
        service_hours.station_measured_hour_key is not null as is_service_hour
    from {{ ref('int_station_headways') }} as headways
    cross join
        unnest(
            sequence(
                date_trunc('hour', least(headways.previous_london_ts, headways.london_ts)),
                date_trunc('hour', greatest(headways.previous_london_ts, headways.london_ts)),
                interval '1' hour
            )
        ) as spanned_hours (hour_start)
    left join {{ ref('int_station_measured_hours') }} as service_hours
        on
            headways.line_id = service_hours.line_id
            and headways.naptan_id = service_hours.naptan_id
            and headways.direction = service_hours.direction
            and cast(spanned_hours.hour_start as date) = service_hours.london_date
            and hour(spanned_hours.hour_start) = service_hours.london_hour
    where not headways.spans_collection_gap
),

in_service as (
    select
        headway_key,
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        previous_arrival_time,
        arrival_time,
        headway_seconds
    from spanned
    group by
        headway_key,
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        previous_arrival_time,
        arrival_time,
        headway_seconds
    having count_if(is_service_hour) = count(*)
)

select
    in_service.headway_key,
    in_service.line_id,
    in_service.naptan_id,
    in_service.direction,
    in_service.london_date,
    in_service.london_hour,
    ending_hour.week_start,
    in_service.previous_arrival_time,
    in_service.arrival_time,
    in_service.headway_seconds,
    ending_hour.is_scheduled,
    ending_hour.scheduled_max_headway_seconds,
    ending_hour.is_in_band,
    in_service.headway_seconds > 2 * ending_hour.scheduled_max_headway_seconds as is_long_gap
from in_service
inner join {{ ref('int_station_measured_hours') }} as ending_hour
    on
        in_service.line_id = ending_hour.line_id
        and in_service.naptan_id = ending_hour.naptan_id
        and in_service.direction = ending_hour.direction
        and in_service.london_date = ending_hour.london_date
        and in_service.london_hour = ending_hour.london_hour
