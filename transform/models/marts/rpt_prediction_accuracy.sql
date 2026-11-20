{{
    config(
        materialized='table',
    )
}}

-- Countdown accuracy per line, London week and horizon (D71, pending): how
-- far TfL's countdown drifts before the train arrives. Not published.
--
-- Percentiles are nearest-rank (the smallest value with at least that
-- share of values at or below it), so they are exact, deterministic and
-- can be checked by hand; with an even count the median is the lower of
-- the middle two.

with errors as (
    select
        week_start,
        london_date,
        line_id,
        horizon_minutes,
        is_matched,
        predicted_seconds,
        actual_seconds,
        signed_error_seconds,
        abs_error_seconds
    from {{ ref('int_prediction_errors') }}
),

ranked as (
    select
        week_start,
        line_id,
        horizon_minutes,
        predicted_seconds,
        actual_seconds,
        signed_error_seconds,
        abs_error_seconds,
        count(*) over line_horizon as group_size,
        row_number() over (line_horizon order by abs_error_seconds) as abs_rank,
        row_number() over (line_horizon order by signed_error_seconds) as signed_rank
    from errors
    where is_matched
    window line_horizon as (partition by week_start, line_id, horizon_minutes)
),

matched_stats as (
    select
        week_start,
        line_id,
        horizon_minutes,
        count(*) as matched_predictions,
        min(if(abs_rank >= ceil(0.5 * group_size), abs_error_seconds))
            as median_abs_error_seconds,
        min(if(abs_rank >= ceil(0.9 * group_size), abs_error_seconds))
            as p90_abs_error_seconds,
        min(if(signed_rank >= ceil(0.5 * group_size), signed_error_seconds))
            as median_signed_error_seconds,
        count_if(abs_error_seconds <= 60) * 1e0 / count(*) as within_60s_share,
        -- How long a minute on this countdown lasts: real seconds per
        -- countdown minute, over every matched prediction.
        60 * sum(actual_seconds) / sum(predicted_seconds) as countdown_minute_seconds
    from ranked
    group by week_start, line_id, horizon_minutes
),

eligible as (
    select
        week_start,
        line_id,
        horizon_minutes,
        count(*) as eligible_arrivals,
        count(distinct london_date) as days_with_arrivals
    from errors
    group by week_start, line_id, horizon_minutes
)

select
    {{ row_key(['eligible.week_start', 'eligible.line_id', 'eligible.horizon_minutes']) }}
        as prediction_accuracy_key,
    eligible.week_start,
    eligible.line_id,
    eligible.horizon_minutes,
    eligible.days_with_arrivals,
    eligible.eligible_arrivals,
    coalesce(matched_stats.matched_predictions, 0) as matched_predictions,
    coalesce(matched_stats.matched_predictions, 0) * 1e0 / eligible.eligible_arrivals
        as matched_share,
    matched_stats.median_abs_error_seconds,
    matched_stats.p90_abs_error_seconds,
    matched_stats.median_signed_error_seconds,
    matched_stats.within_60s_share,
    matched_stats.countdown_minute_seconds,
    coalesce(matched_stats.matched_predictions, 0)
    >= {{ var('prediction_accuracy_min_matched') }} as is_shown
from eligible
left join matched_stats
    on
        eligible.week_start = matched_stats.week_start
        and eligible.line_id = matched_stats.line_id
        and eligible.horizon_minutes = matched_stats.horizon_minutes
