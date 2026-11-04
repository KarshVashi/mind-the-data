{{
    config(
        unique_key='route_stop_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('ref_route_sequence') }}
),

routes as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        route.route_ordinal,
        route.route_json,
        json_parse(envelopes.payload) as sequence_json
    from envelopes
    cross join
        unnest(
            cast(json_extract(json_parse(envelopes.payload), '$.orderedLineRoutes') as array(json))
        ) with ordinality as route (route_json, route_ordinal)
),

route_stops as (
    select
        routes.poll_id,
        routes.polled_at,
        routes.request_path,
        routes.dt,
        routes.sequence_json,
        routes.route_ordinal,
        routes.route_json,
        stop.naptan_id,
        stop.stop_sequence
    from routes
    cross join
        unnest(cast(json_extract(routes.route_json, '$.naptanIds') as array(varchar)))
        with ordinality as stop (naptan_id, stop_sequence)
)

select
    poll_id,
    request_path,
    polled_at,
    json_extract_scalar(sequence_json, '$.lineId') as line_id,
    {{ json_text('sequence_json', '$.lineName') }} as line_name,
    {{ json_text('sequence_json', '$.mode') }} as mode_name,
    json_extract_scalar(sequence_json, '$.direction') as direction,
    route_ordinal,
    trim(json_extract_scalar(route_json, '$.name')) as route_name,
    {{ json_text('route_json', '$.serviceType') }} as service_type,
    stop_sequence,
    naptan_id,
    dt,
    {{ row_key(['poll_id', 'request_path', 'route_ordinal', 'stop_sequence']) }}
        as route_stop_key
from route_stops
