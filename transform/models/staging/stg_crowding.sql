{{
    config(
        enabled=var('context_feeds_enabled'),
        unique_key='crowding_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('crowding') }}
),

parsed as (
    select
        poll_id,
        polled_at,
        request_path,
        dt,
        -- The station is the one asked about: TfL's answer does not name it.
        regexp_extract(request_path, '^/crowding/([^/]+)/Live$', 1) as naptan_id,
        try_cast(json_extract_scalar(payload, '$.dataAvailable') as boolean)
            as is_data_available,
        try_cast(json_extract_scalar(payload, '$.percentageOfBaseline') as double)
            as reported_share,
        {{ tfl_timestamp("json_extract_scalar(payload, '$.timeUtc')") }} as measured_at
    from envelopes
)

select
    poll_id,
    request_path,
    polled_at,
    naptan_id,
    coalesce(is_data_available, false) as is_data_available,
    -- Without data TfL sends a share of 0: that is unknown, not an empty station.
    if(coalesce(is_data_available, false), reported_share, null) as share_of_baseline,
    if(coalesce(is_data_available, false), measured_at, null) as measured_at,
    dt,
    {{ row_key(['poll_id', 'request_path']) }} as crowding_key,
    date_trunc('minute', polled_at) as poll_minute
from parsed
