{{
    config(
        unique_key='severity_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('ref_severity') }}
),

severities as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        severity.severity_ordinal,
        cast(json_extract_scalar(severity.severity_json, '$.severityLevel') as integer)
            as severity_level,
        json_extract_scalar(severity.severity_json, '$.modeName') as mode_name,
        json_extract_scalar(severity.severity_json, '$.description') as description
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as severity (severity_json, severity_ordinal)
),

deduplicated as (
    select
        *,
        -- Codes are per mode; a repeated (mode, level) keeps TfL's first entry.
        -- TfL has not been seen to repeat one; the first wins if it does.
        row_number() over (
            partition by poll_id, request_path, mode_name, severity_level
            order by severity_ordinal
        ) as duplicate_rank
    from severities
)

select
    poll_id,
    request_path,
    polled_at,
    mode_name,
    severity_level,
    description,
    dt,
    {{ row_key(['poll_id', 'request_path', 'mode_name', 'severity_level']) }} as severity_key
from deduplicated
where duplicate_rank = 1
