{{ config(materialized='table', tags=['station']) }}

-- Station-to-station run times (D72): consecutive passes of one vehicle on
-- one line and direction at stations that are next to each other in the
-- line's route sequence, from vehicle ids on the every-station feed
-- (int_station_vehicle_passes). Only lines that link by vehicle id
-- (seed_vehicle_id_lines, D27) are read, so District, DLR and the lines
-- with trip ids are out.
--
-- A pair is dropped when it is not adjacent (a station's pass was missed, or
-- a train ran past it), when the direction changes (a turnback), when the
-- passes are not strictly ordered, or when they are further apart than
-- `segment_max_run_seconds` (a layover or a lost id).
--
-- The times are arrival to arrival: they include the dwell at the first
-- station, and a pass is the expected arrival at the last sighting, so the
-- resolution is the board's, about a minute.
--
-- Rebuilt in full, like int_station_headways: the passes are a small table.

with latest_fetches as (
    -- The latest fetch of each line and direction, as in dim_station.
    select
        line_id,
        direction,
        max_by(poll_id, polled_at) as poll_id,
        max_by(request_path, polled_at) as request_path
    from {{ ref('stg_route_sequence') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by line_id, direction
),

ordered_stops as (
    select
        route_stops.line_id,
        route_stops.direction,
        route_stops.naptan_id,
        lag(route_stops.naptan_id) over (
            partition by route_stops.poll_id, route_stops.request_path, route_stops.route_ordinal
            order by route_stops.stop_sequence
        ) as previous_naptan_id
    from {{ ref('stg_route_sequence') }} as route_stops
    inner join latest_fetches
        on
            route_stops.poll_id = latest_fetches.poll_id
            and route_stops.request_path = latest_fetches.request_path
    where route_stops.dt >= date '{{ var("raw_start_date") }}'
),

adjacent_pairs as (
    -- Branches share stations, so the same pair can come from several routes.
    select distinct
        line_id,
        direction,
        previous_naptan_id as from_naptan_id,
        naptan_id as to_naptan_id
    from ordered_stops
    where
        previous_naptan_id is not null
        and previous_naptan_id <> naptan_id
),

linked_passes as (
    select
        passes.line_id,
        passes.direction,
        passes.vehicle_id,
        passes.naptan_id,
        passes.pass_time
    from {{ ref('int_station_vehicle_passes') }} as passes
    inner join {{ ref('seed_vehicle_id_lines') }} as linked
        on
            passes.line_id = linked.line_id
            and linked.link_by_vehicle_id
),

consecutive_passes as (
    select
        line_id,
        direction,
        vehicle_id,
        naptan_id,
        pass_time,
        lag(naptan_id) over vehicle_passes as previous_naptan_id,
        lag(direction) over vehicle_passes as previous_direction,
        lag(pass_time) over vehicle_passes as previous_pass_time
    from linked_passes
    window vehicle_passes as (
        partition by line_id, vehicle_id
        order by pass_time, naptan_id
    )
),

runs as (
    select
        consecutive_passes.line_id,
        consecutive_passes.direction,
        consecutive_passes.vehicle_id,
        consecutive_passes.previous_naptan_id as from_naptan_id,
        consecutive_passes.naptan_id as to_naptan_id,
        consecutive_passes.previous_pass_time as from_pass_time,
        consecutive_passes.pass_time as to_pass_time,
        date_diff('second', consecutive_passes.previous_pass_time, consecutive_passes.pass_time)
            as run_seconds
    from consecutive_passes
    inner join adjacent_pairs
        on
            consecutive_passes.line_id = adjacent_pairs.line_id
            and consecutive_passes.direction = adjacent_pairs.direction
            and consecutive_passes.previous_naptan_id = adjacent_pairs.from_naptan_id
            and consecutive_passes.naptan_id = adjacent_pairs.to_naptan_id
    where consecutive_passes.previous_direction = consecutive_passes.direction
)

select
    {{ row_key(['line_id', 'vehicle_id', 'from_naptan_id', 'to_naptan_id', 'from_pass_time']) }}
        as segment_run_key,
    line_id,
    direction,
    vehicle_id,
    from_naptan_id,
    to_naptan_id,
    from_pass_time,
    to_pass_time,
    run_seconds,
    cast({{ london_local('from_pass_time') }} as date) as london_date
from runs
where run_seconds > 0 and run_seconds <= {{ var('segment_max_run_seconds') }}
