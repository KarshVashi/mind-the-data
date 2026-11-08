{{
    config(
        unique_key='arrival_prediction_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('arrivals') }}
),

predictions as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        prediction.prediction_ordinal,
        prediction.prediction_json
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as prediction (prediction_json, prediction_ordinal)
),

parsed as (
    select
        poll_id,
        polled_at,
        request_path,
        dt,
        prediction_ordinal,
        json_extract_scalar(prediction_json, '$.id') as prediction_id,
        {{ json_text('prediction_json', '$.vehicleId') }} as vehicle_id,
        json_extract_scalar(prediction_json, '$.naptanId') as naptan_id,
        {{ json_text('prediction_json', '$.stationName') }} as station_name,
        json_extract_scalar(prediction_json, '$.lineId') as line_id,
        {{ json_text('prediction_json', '$.lineName') }} as line_name,
        {{ json_text('prediction_json', '$.modeName') }} as mode_name,
        {{ json_text('prediction_json', '$.platformName') }} as platform_name,
        {{ json_text('prediction_json', '$.direction') }} as direction,
        {{ json_text('prediction_json', '$.destinationNaptanId') }} as destination_naptan_id,
        {{ json_text('prediction_json', '$.destinationName') }} as destination_name,
        {{ json_text('prediction_json', '$.towards') }} as towards,
        {{ json_text('prediction_json', '$.currentLocation') }} as current_location,
        cast(json_extract_scalar(prediction_json, '$.timeToStation') as integer)
            as time_to_station,
        {{ tfl_timestamp("json_extract_scalar(prediction_json, '$.expectedArrival')") }}
            as expected_arrival,
        {{ tfl_timestamp("json_extract_scalar(prediction_json, '$.timestamp')") }}
            as prediction_timestamp,
        {{ tfl_timestamp("json_extract_scalar(prediction_json, '$.timeToLive')") }}
            as time_to_live
    from predictions
),

deduplicated as (
    select
        *,
        -- TfL's prediction id is not unique on a board: every DLR prediction
        -- at a station shares one id, and a train that terminates and leaves
        -- again keeps its id on two platforms. A row is the same prediction
        -- only when the train, place and time all match; then keep the
        -- freshest copy, then the one TfL listed first.
        row_number() over (
            partition by
                poll_id,
                request_path,
                prediction_id,
                line_id,
                naptan_id,
                platform_name,
                direction,
                vehicle_id,
                destination_naptan_id,
                expected_arrival
            order by prediction_timestamp desc nulls last, prediction_ordinal asc
        ) as duplicate_rank
    from parsed
)

select
    poll_id,
    request_path,
    polled_at,
    prediction_id,
    vehicle_id,
    naptan_id,
    station_name,
    line_id,
    line_name,
    mode_name,
    platform_name,
    direction,
    destination_naptan_id,
    destination_name,
    towards,
    current_location,
    time_to_station,
    expected_arrival,
    prediction_timestamp,
    time_to_live,
    dt,
    {{ row_key([
        'poll_id',
        'request_path',
        'prediction_id',
        'line_id',
        'naptan_id',
        'platform_name',
        'direction',
        'vehicle_id',
        'destination_naptan_id',
        'expected_arrival',
    ]) }} as arrival_prediction_key,
    date_trunc('minute', polled_at) as poll_minute
from deduplicated
where duplicate_rank = 1
