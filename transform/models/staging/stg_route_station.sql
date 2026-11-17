{{
    config(
        unique_key='route_station_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

-- The stations a route sequence lists, with TfL's name and coordinates
-- (the `stations` array of `Line/{id}/Route/Sequence/{direction}`). Station
-- membership of a line comes from stg_route_sequence; this model only
-- describes the stations. Kept apart from stg_route_sequence, whose grain is
-- route x stop and whose columns an incremental merge cannot change.

with envelopes as (
    {{ raw_envelopes('ref_route_sequence') }}
),

stations as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        station.station_ordinal,
        station.station_json,
        json_parse(envelopes.payload) as sequence_json
    from envelopes
    cross join
        unnest(
            {{ json_array_or_null('json_parse(envelopes.payload)', '$.stations') }}
        ) with ordinality as station (station_json, station_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    json_extract_scalar(sequence_json, '$.lineId') as line_id,
    json_extract_scalar(sequence_json, '$.direction') as direction,
    station_ordinal,
    json_extract_scalar(station_json, '$.id') as naptan_id,
    {{ json_text('station_json', '$.name') }} as station_name,
    cast(json_extract_scalar(station_json, '$.lat') as double) as lat,
    cast(json_extract_scalar(station_json, '$.lon') as double) as lon,
    {{ json_text('station_json', '$.stopType') }} as stop_type,
    coalesce(
        cast(json_extract(station_json, '$.modes') as array(varchar)),
        cast(array[] as array(varchar))
    ) as modes,
    dt,
    {{ row_key(['poll_id', 'request_path', 'station_ordinal']) }} as route_station_key
from stations
where station_json is not null
