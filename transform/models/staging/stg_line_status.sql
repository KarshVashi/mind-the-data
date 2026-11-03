{{
    config(
        unique_key='line_status_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('line_status') }}
),

lines as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        line.line_ordinal,
        line.line_json
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
        with ordinality as line (line_json, line_ordinal)
),

status_entries as (
    select
        lines.poll_id,
        lines.polled_at,
        lines.request_path,
        lines.dt,
        lines.line_ordinal,
        lines.line_json,
        status.status_ordinal,
        status.status_json
    from lines
    cross join
        unnest(cast(json_extract(lines.line_json, '$.lineStatuses') as array(json)))
        with ordinality as status (status_json, status_ordinal)
),

validity_periods as (
    select
        status_entries.poll_id,
        status_entries.polled_at,
        status_entries.request_path,
        status_entries.dt,
        status_entries.line_ordinal,
        status_entries.line_json,
        status_entries.status_ordinal,
        status_entries.status_json,
        period.validity_ordinal,
        period.period_json
    from status_entries
    cross join
        unnest({{ json_array_or_null('status_entries.status_json', '$.validityPeriods') }})
        with ordinality as period (period_json, validity_ordinal)
),

parsed as (
    select
        poll_id,
        polled_at,
        request_path,
        dt,
        line_ordinal,
        status_ordinal,
        json_extract_scalar(line_json, '$.id') as line_id,
        json_extract_scalar(line_json, '$.name') as line_name,
        json_extract_scalar(line_json, '$.modeName') as mode_name,
        cast(json_extract_scalar(status_json, '$.statusSeverity') as integer) as status_severity,
        json_extract_scalar(status_json, '$.statusSeverityDescription')
            as status_severity_description,
        {{ json_text('status_json', '$.reason') }} as reason,
        {{ json_text('status_json', '$.disruption.category') }} as disruption_category,
        {{ json_text('status_json', '$.disruption.closureText') }} as closure_text,
        -- Planned means TfL filed the entry's disruption as planned work.
        -- Entries without a disruption (Good Service) are not planned.
        coalesce(
            json_extract_scalar(status_json, '$.disruption.category') = 'PlannedWork', false
        ) as is_planned,
        -- An empty period list unnests to one null row: keep its ordinal null.
        if(period_json is null, null, validity_ordinal) as validity_ordinal,
        {{ tfl_timestamp("json_extract_scalar(period_json, '$.fromDate')") }} as valid_from,
        {{ tfl_timestamp("json_extract_scalar(period_json, '$.toDate')") }} as valid_to,
        cast(json_extract_scalar(period_json, '$.isNow') as boolean) as is_now
    from validity_periods
),

deduplicated as (
    select
        *,
        row_number() over (
            partition by poll_id, request_path, line_id, status_ordinal, validity_ordinal
            order by line_ordinal
        ) as duplicate_rank
    from parsed
)

select
    poll_id,
    request_path,
    polled_at,
    line_id,
    line_name,
    mode_name,
    status_ordinal,
    status_severity,
    status_severity_description,
    reason,
    disruption_category,
    closure_text,
    is_planned,
    validity_ordinal,
    valid_from,
    valid_to,
    is_now,
    dt,
    {{ row_key(['poll_id', 'request_path', 'line_id', 'status_ordinal', 'validity_ordinal']) }}
        as line_status_key,
    date_trunc('minute', polled_at) as poll_minute
from deduplicated
where duplicate_rank = 1
