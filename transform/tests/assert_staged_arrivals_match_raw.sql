-- stg_arrivals must keep (almost) every prediction TfL sent. Column tests
-- pass on a table that silently drops rows: keyed on the prediction id alone,
-- staging lost 84% of DLR rows and up to 38% at Tube termini
-- (docs/data-quality/2026-11-08-week-one.md, Q5). Raw predictions are
-- counted per line and day and compared with staged rows for the same
-- successful requests. The only rows staging may drop are the same
-- prediction listed twice, which the week-one data puts under 0.3% of a
-- line-day, so more than 1% missing fails.
-- The last two UTC days are checked, read as literal `dt` values so
-- partition projection prunes the raw scan (about 50 MB a day). Polls from
-- the last 15 minutes are skipped: within one build, raw can hold a poll
-- that staging has not read yet.

{%- set today = modules.datetime.datetime.now(modules.pytz.utc).date() %}
{%- set first_day = (today - modules.datetime.timedelta(days=2)).isoformat() %}

with envelopes as (
    select
        src.poll_id,
        src.request.path as request_path,  -- noqa: RF01
        src.payload,
        cast(src.dt as date) as dt,
        row_number() over (
            partition by src.poll_id, src.request.path  -- noqa: RF01
            order by src.polled_at, src.payload
        ) as envelope_rank
    from {{ source('raw', 'arrivals') }} as src
    where
        src.dt >= '{{ first_day }}'
        and {{ envelope_is_ok('src.http_status', 'src.error', 'src.payload') }}
        and {{ utc_timestamp('src.polled_at') }}
        < date_add('minute', -15, cast(current_timestamp at time zone 'UTC' as timestamp(6)))
),

raw_rows as (
    select
        envelopes.dt,
        json_extract_scalar(prediction.prediction_json, '$.lineId') as line_id,
        count(*) as raw_predictions
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
            as prediction (prediction_json)
    where envelopes.envelope_rank = 1
    group by
        envelopes.dt,
        json_extract_scalar(prediction.prediction_json, '$.lineId')
),

staged_rows as (
    select
        dt,
        line_id,
        count(*) as staged_predictions
    from {{ ref('stg_arrivals') }}
    where
        dt >= date '{{ first_day }}'
        and polled_at
        < date_add('minute', -15, cast(current_timestamp at time zone 'UTC' as timestamp(6)))
    group by dt, line_id
)

select
    raw_rows.dt,
    raw_rows.line_id,
    raw_rows.raw_predictions,
    coalesce(staged_rows.staged_predictions, 0) as staged_predictions
from raw_rows
left join staged_rows
    on
        raw_rows.dt = staged_rows.dt
        and raw_rows.line_id = staged_rows.line_id
where coalesce(staged_rows.staged_predictions, 0) < 0.99 * raw_rows.raw_predictions
