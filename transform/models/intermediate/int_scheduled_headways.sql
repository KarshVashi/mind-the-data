{{
    config(
        materialized='table',
    )
}}

-- Scheduled service per line, measurement station, direction, day type and
-- service-day hour, from the weekly TfL timetables (stg_timetable), with
-- seed_line_frequencies for station-directions TfL has no timetable for.
--
-- Hours are TfL's London service-day clock: 24 to 27 are the small hours of
-- the next calendar day. Rows of two service days can fall in one London
-- hour (Friday's Night Tube hour 27 and Saturday's hour 3), so the headway
-- sums are kept additive: int_headways adds the rows it matches instead of
-- choosing one. A gap belongs to the hour of the departure that ends it
-- (DECISIONS.md D30), and gaps are not formed across two schedules.
--
-- A schedule named for one weekday ("Tuesdays") carries that weekday in
-- iso_day_of_week and applies on it only; a schedule for the whole day type
-- leaves it null (D67). Summing the single-weekday schedules of one day
-- type would give each of its days several days of trains, so the rows keep
-- the weekday apart. Where a timetable version has both kinds for a day
-- type, the single-weekday schedule wins on its own weekday and the generic
-- one covers the day type's other weekdays, as one row for each: a day then
-- matches exactly one schedule and no train is counted twice.
--
-- Rebuilt in full each run (ADR-012, extended to this model in D24): the
-- timetables are a few hundred thousand rows a year, and a seed edit then
-- reaches every version.

with fetches as (
    -- The latest successful fetch of each timetable per UTC day; a day's
    -- fetch replaces the previous version from that day (D32).
    select
        line_id,
        departure_stop_id,
        direction,
        dt as fetched_dt,
        max(polled_at) as fetched_at,
        max_by(poll_id, polled_at) as poll_id
    from {{ ref('stg_timetable') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by line_id, departure_stop_id, direction, dt
),

versions as (
    select
        line_id,
        departure_stop_id,
        direction,
        poll_id,
        fetched_dt,
        fetched_at,
        -- The first fetch also covers the days collected before it.
        if(
            row_number() over (
                partition by line_id, departure_stop_id, direction order by fetched_dt
            ) = 1,
            null,
            fetched_dt
        ) as valid_from,
        lead(fetched_dt) over (
            partition by line_id, departure_stop_id, direction order by fetched_dt
        ) as valid_to
    from fetches
),

departures as (
    select
        versions.line_id,
        versions.departure_stop_id,
        versions.direction,
        versions.valid_from,
        versions.valid_to,
        versions.fetched_at,
        timetable.schedule_name,
        day_types.day_type,
        day_types.iso_day_of_week,
        timetable.departure_minute_of_service_day
    from versions
    inner join {{ ref('stg_timetable') }} as timetable
        on
            versions.poll_id = timetable.poll_id
            and versions.fetched_dt = timetable.dt
            and versions.line_id = timetable.line_id
            and versions.departure_stop_id = timetable.departure_stop_id
            and versions.direction = timetable.direction
    inner join {{ ref('timetable_schedule_day_types') }} as day_types
        on timetable.schedule_name = day_types.schedule_name
    where timetable.dt >= date '{{ var("raw_start_date") }}'
),

gaps as (
    -- Every listed journey counts, including two in one minute (D31): TfL
    -- gives whole minutes, so their gap is under a minute, entered as 0.
    select
        line_id,
        departure_stop_id,
        direction,
        valid_from,
        valid_to,
        fetched_at,
        schedule_name,
        day_type,
        iso_day_of_week,
        departure_minute_of_service_day / 60 as service_hour,
        departure_minute_of_service_day - lag(departure_minute_of_service_day) over (
            partition by
                line_id, departure_stop_id, direction, valid_from, day_type, schedule_name
            order by departure_minute_of_service_day
        ) as headway_minutes
    from departures
),

pooled as (
    select
        line_id,
        departure_stop_id as naptan_id,
        direction,
        day_type,
        iso_day_of_week,
        service_hour,
        valid_from,
        valid_to,
        count(*) as scheduled_departures,
        count(headway_minutes) as headway_count,
        coalesce(sum(60e0 * headway_minutes), 0e0) as sum_headway_seconds,
        coalesce(sum(3600e0 * headway_minutes * headway_minutes), 0e0)
            as sum_headway_squared_seconds,
        -- An hour whose only gaps are same-minute pairs has no usable
        -- scheduled headway: 0 would make every observed gap "long".
        nullif(60e0 * max(headway_minutes), 0e0) as max_headway_seconds,
        'timetable' as schedule_source,
        max(schedule_name) as source_detail,
        max(fetched_at) as timetable_fetched_at
    from gaps
    group by
        line_id,
        departure_stop_id,
        direction,
        day_type,
        iso_day_of_week,
        service_hour,
        valid_from,
        valid_to
),

-- The weekdays a version has a schedule of their own for.
specific_weekdays as (
    select distinct
        line_id,
        naptan_id,
        direction,
        day_type,
        valid_from,
        iso_day_of_week
    from pooled
    where iso_day_of_week is not null
),

timetabled as (
    -- Single-weekday rows, and generic rows of a day type with no
    -- single-weekday schedule in the version, as they are.
    select pooled.*
    from pooled
    where
        pooled.iso_day_of_week is not null
        or not exists (
            select 1
            from specific_weekdays
            where
                specific_weekdays.line_id = pooled.line_id
                and specific_weekdays.naptan_id = pooled.naptan_id
                and specific_weekdays.direction = pooled.direction
                and specific_weekdays.day_type = pooled.day_type
                and specific_weekdays.valid_from is not distinct from pooled.valid_from
        )

    union all

    -- A generic row of a day type that has single-weekday schedules: one
    -- copy for each weekday of the day type that has none of its own.
    select
        pooled.line_id,
        pooled.naptan_id,
        pooled.direction,
        pooled.day_type,
        weekdays.iso_day_of_week,
        pooled.service_hour,
        pooled.valid_from,
        pooled.valid_to,
        pooled.scheduled_departures,
        pooled.headway_count,
        pooled.sum_headway_seconds,
        pooled.sum_headway_squared_seconds,
        pooled.max_headway_seconds,
        pooled.schedule_source,
        pooled.source_detail,
        pooled.timetable_fetched_at
    from pooled
    inner join {{ ref('day_types') }} as weekdays
        on pooled.day_type = weekdays.day_type
    where
        pooled.iso_day_of_week is null
        and exists (
            select 1
            from specific_weekdays
            where
                specific_weekdays.line_id = pooled.line_id
                and specific_weekdays.naptan_id = pooled.naptan_id
                and specific_weekdays.direction = pooled.direction
                and specific_weekdays.day_type = pooled.day_type
                and specific_weekdays.valid_from is not distinct from pooled.valid_from
        )
        and not exists (
            select 1
            from specific_weekdays
            where
                specific_weekdays.line_id = pooled.line_id
                and specific_weekdays.naptan_id = pooled.naptan_id
                and specific_weekdays.direction = pooled.direction
                and specific_weekdays.day_type = pooled.day_type
                and specific_weekdays.valid_from is not distinct from pooled.valid_from
                and specific_weekdays.iso_day_of_week = weekdays.iso_day_of_week
        )
),

timetabled_directions as (
    select distinct
        line_id,
        departure_stop_id as naptan_id,
        direction
    from {{ ref('stg_timetable') }}
    where dt >= date '{{ var("raw_start_date") }}'
),

-- A flat frequency f gives f gaps of 3600 / f seconds in each hour.
fallback as (
    select
        frequencies.line_id,
        frequencies.naptan_id,
        directions.direction,
        frequencies.day_type,
        cast(null as integer) as iso_day_of_week,
        hours.service_hour,
        cast(null as date) as valid_from,
        cast(null as date) as valid_to,
        cast(frequencies.trains_per_hour as bigint) as scheduled_departures,
        cast(frequencies.trains_per_hour as bigint) as headway_count,
        3600e0 as sum_headway_seconds,
        3600e0 * 3600e0 / frequencies.trains_per_hour as sum_headway_squared_seconds,
        3600e0 / frequencies.trains_per_hour as max_headway_seconds,
        'seed_line_frequencies' as schedule_source,
        frequencies.source as source_detail,
        cast(null as timestamp(6)) as timetable_fetched_at
    from {{ ref('seed_line_frequencies') }} as frequencies
    cross join unnest(array['inbound', 'outbound']) as directions (direction)
    cross join
        unnest(sequence(frequencies.from_hour, frequencies.to_hour - 1))
            as hours (service_hour)
    left join timetabled_directions
        on
            frequencies.line_id = timetabled_directions.line_id
            and frequencies.naptan_id = timetabled_directions.naptan_id
            and directions.direction = timetabled_directions.direction
    where
        frequencies.direction in (directions.direction, 'both')
        and timetabled_directions.line_id is null
        and frequencies.trains_per_hour > 0
),

combined as (
    select * from timetabled
    union all
    select * from fallback
)

select
    line_id,
    naptan_id,
    direction,
    day_type,
    iso_day_of_week,
    service_hour,
    service_hour % 24 as london_hour,
    service_hour >= 24 as is_next_calendar_day,
    valid_from,
    valid_to,
    scheduled_departures,
    headway_count,
    sum_headway_seconds,
    sum_headway_squared_seconds,
    if(sum_headway_seconds > 0, sum_headway_seconds / headway_count) as mean_headway_seconds,
    max_headway_seconds,
    -- PLAN 7.3: expected wait for a passenger arriving at random.
    if(sum_headway_seconds > 0, sum_headway_squared_seconds / (2 * sum_headway_seconds))
        as scheduled_average_wait_seconds,
    schedule_source,
    schedule_source = 'seed_line_frequencies' as is_fallback,
    source_detail,
    timetable_fetched_at,
    {{ row_key([
        'line_id',
        'naptan_id',
        'direction',
        'day_type',
        'iso_day_of_week',
        'service_hour',
        'valid_from',
    ]) }}
        as scheduled_headway_key
from combined
