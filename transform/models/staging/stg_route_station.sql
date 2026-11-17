{{
    config(
        tags=['station'],
        unique_key='route_station_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

-- The stop points a route sequence describes, with TfL's name and
-- coordinates (`stopPointSequences[].stopPoint[]` of
-- `Line/{id}/Route/Sequence/{direction}`). These carry the same ids as the
-- routes' `naptanIds`; the top-level `stations` list does not (it gives hub
-- ids such as HUBABW for some interchanges). Line membership comes from
-- stg_route_sequence; this model only describes the stations. Kept apart
-- from stg_route_sequence, whose columns an incremental merge cannot change.

with envelopes as (
    {{ raw_envelopes('ref_route_sequence') }}
),

sequences as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        stop_sequence.sequence_ordinal,
        stop_sequence.sequence_json,
        json_parse(envelopes.payload) as payload_json
    from envelopes
    cross join
        unnest(
            {{ json_array_or_null('json_parse(envelopes.payload)', '$.stopPointSequences') }}
        ) with ordinality as stop_sequence (sequence_json, sequence_ordinal)
),

stops as (
    select
        sequences.poll_id,
        sequences.polled_at,
        sequences.request_path,
        sequences.dt,
        sequences.payload_json,
        sequences.sequence_ordinal,
        stop.stop_ordinal,
        stop.stop_json
    from sequences
    cross join
        unnest(
            {{ json_array_or_null('sequences.sequence_json', '$.stopPoint') }}
        ) with ordinality as stop (stop_json, stop_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    json_extract_scalar(payload_json, '$.lineId') as line_id,
    json_extract_scalar(payload_json, '$.direction') as direction,
    sequence_ordinal,
    stop_ordinal,
    json_extract_scalar(stop_json, '$.id') as naptan_id,
    {{ json_text('stop_json', '$.name') }} as station_name,
    cast(json_extract_scalar(stop_json, '$.lat') as double) as lat,
    cast(json_extract_scalar(stop_json, '$.lon') as double) as lon,
    {{ json_text('stop_json', '$.stopType') }} as stop_type,
    {{ json_text('stop_json', '$.topMostParentId') }} as top_most_parent_id,
    dt,
    {{ row_key(['poll_id', 'request_path', 'sequence_ordinal', 'stop_ordinal']) }}
        as route_station_key
from stops
where stop_json is not null
