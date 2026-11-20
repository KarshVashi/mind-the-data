{{
    config(
        enabled=var('context_feeds_enabled'),
        unique_key='bike_point_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('bike_point') }}
),

-- A 200 whose body is not a JSON array (e.g. `null`) is missing data, as a
-- failed request is: it must never read as an empty list.
array_envelopes as (
    select *
    from envelopes
    where json_array_length(json_parse(payload)) is not null
),

docks as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        dock.dock_json,
        dock.dock_ordinal
    from array_envelopes as envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as dock (dock_json, dock_ordinal)
),

parsed as (
    select
        poll_id,
        polled_at,
        request_path,
        dt,
        dock_ordinal,
        json_extract_scalar(dock_json, '$.id') as bike_point_id,
        {{ json_text('dock_json', '$.commonName') }} as common_name,
        try_cast(json_extract_scalar(dock_json, '$.lat') as double) as latitude,
        try_cast(json_extract_scalar(dock_json, '$.lon') as double) as longitude,
        -- Counts are text properties; a missing or garbled one is null, never 0.
        try_cast({{ place_property('dock_json', 'NbBikes') }} as integer) as bikes,
        try_cast({{ place_property('dock_json', 'NbStandardBikes') }} as integer)
            as standard_bikes,
        try_cast({{ place_property('dock_json', 'NbEBikes') }} as integer) as e_bikes,
        try_cast({{ place_property('dock_json', 'NbEmptyDocks') }} as integer) as empty_docks,
        try_cast({{ place_property('dock_json', 'NbDocks') }} as integer) as docks,
        try_cast({{ place_property('dock_json', 'Installed') }} as boolean) as is_installed,
        try_cast({{ place_property('dock_json', 'Locked') }} as boolean) as is_locked,
        try_cast({{ place_property('dock_json', 'Temporary') }} as boolean) as is_temporary,
        {{ tfl_timestamp(place_property('dock_json', 'NbBikes', 'modified')) }}
            as counts_modified_at
    from docks
)

select
    poll_id,
    request_path,
    polled_at,
    dock_ordinal,
    bike_point_id,
    common_name,
    latitude,
    longitude,
    bikes,
    standard_bikes,
    e_bikes,
    empty_docks,
    docks,
    is_installed,
    is_locked,
    is_temporary,
    counts_modified_at,
    dt,
    {{ row_key(['poll_id', 'request_path', 'bike_point_id', 'dock_ordinal']) }} as bike_point_key,
    date_trunc('minute', polled_at) as poll_minute
from parsed
