{{
    config(
        unique_key='stop_disruption_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('stop_disruption') }}
),

disrupted_points as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        point.disruption_ordinal,
        point.point_json
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as point (point_json, disruption_ordinal)
),

concerned_lines as (
    select
        disrupted_points.poll_id,
        disrupted_points.polled_at,
        disrupted_points.request_path,
        disrupted_points.dt,
        disrupted_points.disruption_ordinal,
        disrupted_points.point_json,
        concerned.line_json,
        concerned.line_ordinal
    from disrupted_points
    cross join
        unnest({{ json_array_or_null('disrupted_points.point_json', '$.concernedLines') }})
        with ordinality as concerned (line_json, line_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    -- TfL gives disrupted points no id, so the position in the response is the key.
    disruption_ordinal,
    json_extract_scalar(point_json, '$.atcoCode') as atco_code,
    {{ json_text('point_json', '$.stationAtcoCode') }} as station_atco_code,
    {{ json_text('point_json', '$.commonName') }} as common_name,
    {{ json_text('point_json', '$.mode') }} as mode_name,
    {{ json_text('point_json', '$.type') }} as disruption_type,
    {{ json_text('point_json', '$.appearance') }} as appearance,
    {{ json_text('point_json', '$.description') }} as description,
    {{ json_text('point_json', '$.additionalInformation') }} as additional_information,
    coalesce(json_extract_scalar(point_json, '$.appearance') = 'PlannedWork', false)
        as is_planned,
    {{ tfl_timestamp("json_extract_scalar(point_json, '$.fromDate')") }} as valid_from,
    {{ tfl_timestamp("json_extract_scalar(point_json, '$.toDate')") }} as valid_to,
    -- An empty concerned-lines list unnests to one null row: keep its ordinal null.
    if(line_json is null, null, line_ordinal) as line_ordinal,
    json_extract_scalar(line_json, '$.id') as line_id,
    {{ json_text('line_json', '$.direction') }} as line_direction,
    dt,
    {{ row_key(['poll_id', 'request_path', 'disruption_ordinal', 'line_ordinal']) }}
        as stop_disruption_key,
    date_trunc('minute', polled_at) as poll_minute
from concerned_lines
