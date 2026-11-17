{{ config(materialized='table') }}

-- Every station on every line in scope (M8), one row per station-level
-- NaPTAN id. Membership comes from each line's latest route sequences, so
-- a station a line stops serving drops off at the next weekly fetch. Name
-- and coordinates come from the latest route sequence that lists the
-- station, whichever line it was. Ids are TfL's: an interchange with both
-- a 940G (Underground, DLR, tram) and a 910G (rail) id is two stations
-- here, as it is in the arrivals and disruption feeds.

with latest_fetches as (
    -- The latest fetch of each line and direction; the union of both
    -- directions is the line's station list.
    select
        line_id,
        direction,
        max_by(poll_id, polled_at) as poll_id,
        max_by(request_path, polled_at) as request_path
    from {{ ref('stg_route_sequence') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by line_id, direction
),

line_stations as (
    select distinct
        route_stops.line_id,
        route_stops.naptan_id,
        scope_lines.mode_name
    from {{ ref('stg_route_sequence') }} as route_stops
    inner join latest_fetches
        on
            route_stops.poll_id = latest_fetches.poll_id
            and route_stops.request_path = latest_fetches.request_path
    -- Only lines in scope; a route sequence names no other line, but the
    -- seed is what makes a line "in scope".
    inner join {{ ref('lines') }} as scope_lines
        on route_stops.line_id = scope_lines.line_id
    where route_stops.dt >= date '{{ var("raw_start_date") }}'
),

described as (
    select
        naptan_id,
        max_by(station_name, polled_at) as tfl_station_name,
        max_by(lat, polled_at) as lat,
        max_by(lon, polled_at) as lon
    from {{ ref('stg_route_station') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by naptan_id
),

stations as (
    select
        naptan_id,
        array_sort(array_agg(distinct line_id)) as line_ids,
        array_sort(array_agg(distinct mode_name)) as modes
    from line_stations
    group by naptan_id
),

measurement_stations as (
    select distinct naptan_id
    from {{ ref('seed_measurement_stations') }}
)

select
    stations.naptan_id,
    described.tfl_station_name,
    described.lat,
    described.lon,
    stations.line_ids,
    stations.modes,
    -- TfL suffixes every name with its stop type; the map labels the place.
    regexp_replace(
        described.tfl_station_name,
        '\s+(Underground Station|Rail Station|DLR Station|Tram Stop)$',
        ''
    ) as station_name,
    measurement_stations.naptan_id is not null as is_measurement_station
from stations
left join described
    on stations.naptan_id = described.naptan_id
left join measurement_stations
    on stations.naptan_id = measurement_stations.naptan_id
