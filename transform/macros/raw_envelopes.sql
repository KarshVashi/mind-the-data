{# Raw feeds, one Glue table each in the `raw` source. #}
{% macro raw_feeds() %}
    {{ return([
        'line_status',
        'line_disruption',
        'arrivals',
        'stop_disruption',
        'ref_route_sequence',
        'ref_timetable',
        'ref_severity',
    ]) }}
{% endmacro %}


{#
  A request succeeded only if TfL answered 2xx, the collector recorded no
  error and there is a payload to read. Anything else is missing data, never
  an empty (and so "good") response.
#}
{% macro envelope_is_ok(http_status='http_status', error='error', payload='payload') %}
    coalesce({{ http_status }} between 200 and 299 and {{ error }} is null and {{ payload }} is not null, false)
{%- endmacro %}


{#
  Predicate on a raw table's `dt` partition for the current window.
  Kept out of staging_window.sql: sqlfluff's jinja templater only honours a
  macro's return() when it is called from another file.
#}
{% macro raw_dt_filter() %}
    {%- set window = staging_window() -%}
    dt between '{{ window.start }}' and '{{ window.end }}'
{%- endmacro %}


{#
  Envelopes of one raw feed inside the staging window, one row per
  (poll_id, request_path).

  The same envelope can appear twice if an object is rewritten or a write is
  retried with the same poll_id; the successful copy, then the earliest, is
  kept. Polls retried under a new poll_id are distinct polls and both stay;
  the minute-level models choose between them.

  successful_only=true keeps only envelopes whose payload can be parsed.
#}
{% macro raw_envelopes(feed, successful_only=true) %}
    select
        ranked.poll_id,
        ranked.feed,
        ranked.polled_at,
        ranked.request_path,
        ranked.http_status,
        ranked.latency_ms,
        ranked.error,
        ranked.payload,
        ranked.is_ok,
        ranked.dt
    from (
        select
            src.poll_id,
            src.feed,
            {{ utc_timestamp('src.polled_at') }} as polled_at,
            src.request.path as request_path,
            src.http_status,
            src.latency_ms,
            src.error,
            src.payload,
            {{ envelope_is_ok('src.http_status', 'src.error', 'src.payload') }} as is_ok,
            cast(src.dt as date) as dt,
            row_number() over (
                partition by src.poll_id, src.request.path
                order by
                    {{ envelope_is_ok('src.http_status', 'src.error', 'src.payload') }} desc,
                    src.polled_at,
                    src.latency_ms,
                    src.http_status,
                    -- Total order, so a rerun picks the same copy and
                    -- position-keyed rows never mix two copies.
                    src.error,
                    src.payload
            ) as envelope_rank
        from {{ source('raw', feed) }} as src
        where src.{{ raw_dt_filter() }}
    ) as ranked
    where ranked.envelope_rank = 1
    {%- if successful_only %}
        and ranked.is_ok
    {%- endif %}
{% endmacro %}
