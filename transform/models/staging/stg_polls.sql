{{
    config(
        unique_key='poll_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {% for feed in raw_feeds() %}
        {{ raw_envelopes(feed, successful_only=false) }}
        {{ 'union all' if not loop.last }}
    {% endfor %}
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
