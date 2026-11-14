{{ config(materialized='table') }}

-- Trains seen on the arrival boards, counted by vehicle id (ADR-014). The
-- quality gate compares inferred arrivals with this count instead of the
-- timetable, and observed trains over scheduled trains is the published
-- "trains run" share.
--
-- A visit is a run of sightings of one vehicle id at one station and
-- direction, any platform, with no gap over `vehicle_pass_visit_gap_seconds`
-- between them. A visit is a pass when the train came within
-- `vehicle_pass_threshold_seconds` of the station (time to go read from the
-- poll, as D40). Its time is the expected arrival of its last sighting, the
-- time int_inferred_arrivals gives the same train (PLAN 7.2 rule 3).
--
-- Nothing here links sightings by expected arrival, so it is independent of
-- the inference's linking rules (D26, D27). Placeholder ids are no id, and
-- sightings without an id are left out: int_measured_hours counts those
-- trains from the inference instead. Rebuilt in full, like
-- int_inferred_arrivals (D41).

with sightings as (
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        polled_at,
        expected_arrival,
        greatest(0, date_diff('second', polled_at, expected_arrival)) as seconds_to_arrival
    from {{ ref('stg_arrivals') }}
    where
        dt >= date '{{ var("raw_start_date") }}'
        and direction is not null
        and vehicle_id is not null
        and vehicle_id not in (
            {%- for placeholder in var('placeholder_vehicle_ids') %}
                '{{ placeholder }}'{{ ',' if not loop.last }}
            {%- endfor %}
        )
),

gaps as (
    select
        *,
        lag(polled_at) over vehicle_sightings as previous_polled_at
    from sightings
    window vehicle_sightings as (
        partition by line_id, naptan_id, direction, vehicle_id
        order by polled_at, expected_arrival
    )
),

numbered as (
    select
        *,
        sum(
            if(
                previous_polled_at is null
                or date_diff('millisecond', previous_polled_at, polled_at)
                > {{ var('vehicle_pass_visit_gap_seconds') }} * 1000,
                1,
                0
            )
        ) over (
            partition by line_id, naptan_id, direction, vehicle_id
            order by polled_at, expected_arrival
            rows between unbounded preceding and current row
        ) as visit_number
    from gaps
),

visits as (
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        visit_number,
        min(polled_at) as first_seen_at,
        max(polled_at) as last_seen_at,
        count(*) as sightings,
        min(seconds_to_arrival) as min_seconds_to_arrival,
        max_by(expected_arrival, polled_at) as pass_time
    from numbered
    group by line_id, naptan_id, direction, vehicle_id, visit_number
)

select
    line_id,
    naptan_id,
    direction,
    vehicle_id,
    visit_number,
    first_seen_at,
    last_seen_at,
    sightings,
    min_seconds_to_arrival,
    pass_time,
    cast({{ london_local('pass_time') }} as date) as london_date,
    hour({{ london_local('pass_time') }}) as london_hour,
    {{ row_key(['line_id', 'naptan_id', 'direction', 'vehicle_id', 'first_seen_at']) }}
        as vehicle_pass_key
from visits
where min_seconds_to_arrival <= {{ var('vehicle_pass_threshold_seconds') }}
