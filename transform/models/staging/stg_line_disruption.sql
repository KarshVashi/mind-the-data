{{
    config(
        unique_key='line_disruption_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('line_disruption') }}
),

disruptions as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        disruption.disruption_ordinal,
        disruption.disruption_json
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as disruption (disruption_json, disruption_ordinal)
)

select
    poll_id,
    request_path,
    polled_at,
    -- TfL gives disruptions no id, so the position in the response is the key.
    disruption_ordinal,
    json_extract_scalar(disruption_json, '$.category') as category,
    {{ json_text('disruption_json', '$.categoryDescription') }} as category_description,
    {{ json_text('disruption_json', '$.type') }} as disruption_type,
    json_extract_scalar(disruption_json, '$.description') as description,
    {{ json_text('disruption_json', '$.summary') }} as summary,
    {{ json_text('disruption_json', '$.additionalInfo') }} as additional_info,
    {{ json_text('disruption_json', '$.closureText') }} as closure_text,
    coalesce(json_extract_scalar(disruption_json, '$.category') = 'PlannedWork', false)
        as is_planned,
    -- The payload names lines only through affected routes; the description
    -- text ("Bakerloo Line: ...") is the fallback for int_ models.
    array_distinct(
        filter(
            transform(
                coalesce(
                    cast(json_extract(disruption_json, '$.affectedRoutes') as array(json)),
                    cast(array[] as array(json))
                ),
                affected_route -> json_extract_scalar(affected_route, '$.lineId')
            ),
            affected_line_id -> affected_line_id is not null
        )
    ) as affected_line_ids,
    {{ tfl_timestamp("json_extract_scalar(disruption_json, '$.created')") }} as created_at,
    {{ tfl_timestamp("json_extract_scalar(disruption_json, '$.lastUpdate')") }}
        as last_updated_at,
    dt,
    {{ row_key(['poll_id', 'request_path', 'disruption_ordinal']) }} as line_disruption_key,
    date_trunc('minute', polled_at) as poll_minute
from disruptions
