{{ config(materialized='table', tags=['station']) }}

-- Whether a station is an end of a line (D70), one row per line and
-- station on the line's latest route sequences, as dim_station reads them,
-- plus each seeded terminus those sequences do not list.
-- A terminus is the first or last stop of any of the line's ordered routes,
-- which takes in the end of every branch and of a route that trains turn
-- back on, or a station seed_measurement_stations marks `terminus` (D66).
-- It is the stop where some of the line's scheduled trains end their trip.
-- A seeded terminus counts on its own, even when the latest route sequence
-- does not list it. A turnback that no route ends at is not a terminus here,
-- whatever D65 does with it.
--
-- Why it matters: at an end of the line TfL lists departing trains with no
-- direction (Uxbridge, Amersham, Wimbledon), which the inference never
-- counts, and arriving trains on more than one platform (Stratford,
-- Richmond), which it counts twice. The station's trains run and its gaps
-- are then wrong, so fct_station_weekly_measured withholds them.

with latest_fetches as (
    select
        line_id,
        direction,
        max_by(poll_id, polled_at) as poll_id,
        max_by(request_path, polled_at) as request_path
    from {{ ref('stg_route_sequence') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by line_id, direction
),

route_stops as (
    select
        route_stops.line_id,
        route_stops.naptan_id,
        route_stops.stop_sequence,
        max(route_stops.stop_sequence) over (
            partition by route_stops.poll_id, route_stops.request_path, route_stops.route_ordinal
        ) as last_stop_sequence
    from {{ ref('stg_route_sequence') }} as route_stops
    inner join latest_fetches
        on
            route_stops.poll_id = latest_fetches.poll_id
            and route_stops.request_path = latest_fetches.request_path
    inner join {{ ref('lines') }} as scope_lines
        on route_stops.line_id = scope_lines.line_id
    where route_stops.dt >= date '{{ var("raw_start_date") }}'
),

line_stations as (
    select
        line_id,
        naptan_id,
        bool_or(stop_sequence = 1 or stop_sequence = last_stop_sequence) as is_route_end
    from route_stops
    group by line_id, naptan_id
),

seed_termini as (
    -- Lines in scope only, as on the route side.
    select distinct
        seed_stations.line_id,
        seed_stations.naptan_id
    from {{ ref('seed_measurement_stations') }} as seed_stations
    inner join {{ ref('lines') }} as scope_lines
        on seed_stations.line_id = scope_lines.line_id
    where seed_stations.role = 'terminus'
)

select
    coalesce(line_stations.line_id, seed_termini.line_id) as line_id,
    coalesce(line_stations.naptan_id, seed_termini.naptan_id) as naptan_id,
    coalesce(line_stations.is_route_end, false) or seed_termini.naptan_id is not null
        as is_terminus,
    case
        when line_stations.is_route_end and seed_termini.naptan_id is not null then 'route_and_seed'
        when line_stations.is_route_end then 'route'
        when seed_termini.naptan_id is not null then 'seed'
    end as terminus_source,
    {{ row_key([
        'coalesce(line_stations.line_id, seed_termini.line_id)',
        'coalesce(line_stations.naptan_id, seed_termini.naptan_id)'
    ]) }} as line_station_key
from line_stations
full outer join seed_termini
    on
        line_stations.line_id = seed_termini.line_id
        and line_stations.naptan_id = seed_termini.naptan_id
