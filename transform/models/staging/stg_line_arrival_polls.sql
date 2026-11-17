{{
    config(
        tags=['station'],
        unique_key='poll_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

-- Every line_arrivals request, failed ones included, in the shape of
-- stg_polls. Kept out of stg_polls so the line-level models never depend on
-- the line feed (ADR-016): if its raw table is missing or late, only the
-- station models fail. Successful polls end trains in the inference and
-- decide which station hours were observed; failed ones are missing data.

with envelopes as (
    {{ raw_envelopes(
        'line_arrivals', successful_only=false, initial_days=var('station_initial_days'),
    ) }}
)

select
    poll_id,
    feed,
    request_path,
    polled_at,
    http_status,
    latency_ms,
    error,
    is_ok,
    dt,
    {{ row_key(['poll_id', 'request_path']) }} as poll_key,
    date_trunc('minute', polled_at) as poll_minute
from envelopes
