-- stg_arrivals must hold exactly one row per distinct raw prediction.
-- Column tests pass on a table that silently drops rows: keyed on the
-- prediction id alone, staging lost 84% of DLR rows and up to 38% at Tube
-- termini (docs/data-quality/2026-11-08-week-one.md, Q5). Raw predictions
-- are counted per line and day, a prediction being the id plus line, stop,
-- platform, direction, vehicle, destination and expected arrival (D44), and
-- compared with staged rows. TfL does list one prediction twice (8% of
-- Piccadilly rows overnight, one copy without currentLocation), so raw
-- rows are not counted one for one. Fewer staged rows means a key narrower
-- than a prediction; more means rows left behind by an older key (a merge
-- after a key change without a full refresh, D44).
-- Today and the two UTC days before it are checked, read as literal `dt` values so
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
        coalesce(json_extract_scalar(prediction.prediction_json, '$.lineId'), '<null>')
            as line_id,
        count(distinct row(
            envelopes.poll_id,
            envelopes.request_path,
            json_extract_scalar(prediction.prediction_json, '$.id'),
            json_extract_scalar(prediction.prediction_json, '$.naptanId'),
            nullif(json_extract_scalar(prediction.prediction_json, '$.platformName'), ''),
            nullif(json_extract_scalar(prediction.prediction_json, '$.direction'), ''),
            nullif(json_extract_scalar(prediction.prediction_json, '$.vehicleId'), ''),
            nullif(json_extract_scalar(prediction.prediction_json, '$.destinationNaptanId'), ''),
            json_extract_scalar(prediction.prediction_json, '$.expectedArrival')
        )) as raw_predictions
    from envelopes
    cross join
        unnest(cast(json_parse(envelopes.payload) as array(json)))
            as prediction (prediction_json)
    where envelopes.envelope_rank = 1
    group by
        envelopes.dt,
        coalesce(json_extract_scalar(prediction.prediction_json, '$.lineId'), '<null>')
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
    coalesce(raw_rows.dt, staged_rows.dt) as dt,
    coalesce(raw_rows.line_id, staged_rows.line_id) as line_id,
    coalesce(raw_rows.raw_predictions, 0) as raw_predictions,
    coalesce(staged_rows.staged_predictions, 0) as staged_predictions
from raw_rows
full outer join staged_rows
    on
        raw_rows.dt = staged_rows.dt
        and raw_rows.line_id = staged_rows.line_id
where coalesce(staged_rows.staged_predictions, 0) != coalesce(raw_rows.raw_predictions, 0)
