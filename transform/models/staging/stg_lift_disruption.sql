{{
    config(
        enabled=var('context_feeds_enabled'),
        unique_key='lift_disruption_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('lift_disruption') }}
),

-- A 200 whose body is not a JSON array (e.g. `null`) is missing data, as a
-- failed request is: it must never read as an empty list.
array_envelopes as (
    select *
    from envelopes
    where json_array_length(json_parse(payload)) is not null
),

-- An empty list (no lift out of service) keeps one row with null station
-- columns, so every successful poll has a row: a minute without one is
-- missing data, never "every lift working".
stations as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        station.station_json,
        station.station_ordinal
    from array_envelopes as envelopes
    cross join
        unnest({{ json_array_or_null('json_parse(envelopes.payload)', '$') }})
        with ordinality as station (station_json, station_ordinal)
),

lifts as (
    select
        stations.poll_id,
        stations.polled_at,
        stations.request_path,
        stations.dt,
        stations.station_json,
        stations.station_ordinal,
        lift.lift_json,
        lift.lift_ordinal
    from stations
    cross join
        unnest({{ json_array_or_null('stations.station_json', '$.disruptedLiftUniqueIds') }})
        with ordinality as lift (lift_json, lift_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    if(station_json is null, null, station_ordinal) as station_ordinal,
    {{ json_text('station_json', '$.stationUniqueId') }} as station_unique_id,
    if(lift_json is null, null, lift_ordinal) as lift_ordinal,
    nullif(json_extract_scalar(lift_json, '$'), '') as lift_unique_id,
    {{ json_text('station_json', '$.message') }} as message,
    station_json is not null as has_disruption,
    dt,
    {{ row_key(['poll_id', 'request_path', 'station_ordinal', 'lift_ordinal']) }}
        as lift_disruption_key,
    date_trunc('minute', polled_at) as poll_minute
from lifts
