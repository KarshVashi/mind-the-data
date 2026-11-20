{{
    config(
        unique_key='timetable_stop_interval_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

-- The stopping patterns a timetable lists (`timetable.routes[].stationIntervals`):
-- each interval is one pattern of calls after the departure station, in
-- order, with the running time to each. stg_timetable stages the departures;
-- this model stages the calls, so int_board_dropouts can tell a train that
-- is timetabled to run through a station from one the station's boards
-- dropped (D75). Kept apart from stg_timetable, whose columns an
-- incremental merge cannot change.

with envelopes as (
    {{ raw_envelopes('ref_timetable') }}
),

routes as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        route.route_ordinal,
        route.route_json,
        json_parse(envelopes.payload) as timetable_json
    from envelopes
    cross join
        unnest(
            {{ json_array_or_null('json_parse(envelopes.payload)', '$.timetable.routes') }}
        ) with ordinality as route (route_json, route_ordinal)
),

station_intervals as (
    select
        routes.poll_id,
        routes.polled_at,
        routes.request_path,
        routes.dt,
        routes.timetable_json,
        routes.route_ordinal,
        station_interval.interval_ordinal,
        station_interval.interval_json
    from routes
    cross join
        unnest(
            {{ json_array_or_null('routes.route_json', '$.stationIntervals') }}
        ) with ordinality as station_interval (interval_json, interval_ordinal)
),

stops as (
    select
        station_intervals.poll_id,
        station_intervals.polled_at,
        station_intervals.request_path,
        station_intervals.dt,
        station_intervals.timetable_json,
        station_intervals.route_ordinal,
        station_intervals.interval_ordinal,
        station_intervals.interval_json,
        stop.stop_ordinal,
        stop.stop_json
    from station_intervals
    cross join
        unnest(
            {{ json_array_or_null('station_intervals.interval_json', '$.intervals') }}
        ) with ordinality as stop (stop_json, stop_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    json_extract_scalar(timetable_json, '$.lineId') as line_id,
    json_extract_scalar(timetable_json, '$.direction') as direction,
    json_extract_scalar(timetable_json, '$.timetable.departureStopId') as departure_stop_id,
    route_ordinal,
    interval_ordinal,
    try_cast(json_extract_scalar(interval_json, '$.id') as integer) as interval_id,
    stop_ordinal,
    json_extract_scalar(stop_json, '$.stopId') as stop_id,
    cast(json_extract_scalar(stop_json, '$.timeToArrival') as double) as minutes_to_arrival,
    dt,
    {{ row_key(['poll_id', 'request_path', 'route_ordinal', 'interval_ordinal', 'stop_ordinal']) }}
        as timetable_stop_interval_key
from stops
-- A disambiguation, or a route without patterns, unnests to a null stop.
where stop_json is not null
