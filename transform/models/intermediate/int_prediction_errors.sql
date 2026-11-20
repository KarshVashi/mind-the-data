{{
    config(
        tags=['exploratory'],
        materialized='table',
    )
}}

-- How far TfL's countdown drifts before a train arrives (D71, pending).
-- One row per inferred arrival and horizon: the arrival's train, picked by
-- vehicle id, as the same board listed it when its countdown read about
-- that many minutes. Arrivals with no such prediction stay as unmatched
-- rows, so the report can give the share matched.
--
-- The arrival time is itself the train's last prediction before it left
-- the board (PLAN 7.2), so this measures drift within TfL's own countdown,
-- not accuracy against an independent clock. The sighting that sets the
-- arrival time is never scored against itself.
--
-- The measurement-station feed, not the every-station one: it runs from
-- 4 November, so the series is consistent from its first week, and its
-- stations carry a role, so termini can be left out (D66). Rebuilt in full,
-- like the other measured-view intermediate models (D41).

with vehicle_id_lines as (
    -- Only lines whose vehicle ids follow one train (D27): elsewhere an id
    -- could match another train's prediction.
    select line_id
    from {{ ref('seed_vehicle_id_lines') }}
    where link_by_vehicle_id
),

stations as (
    -- At a terminus TfL lists departing trains oddly or not at all (D66).
    select
        line_id,
        naptan_id,
        role
    from {{ ref('seed_measurement_stations') }}
    where role <> 'terminus'
),

horizons as (
    select horizon_list.horizon_minutes
    from
        unnest(array[
            {%- for horizon in var('prediction_accuracy_horizons') %}
                {{ horizon }}{{ ',' if not loop.last }}
            {%- endfor %}
        ]) as horizon_list (horizon_minutes)
),

arrivals as (
    select
        arrivals.arrival_key,
        arrivals.request_path,
        arrivals.naptan_id,
        arrivals.line_id,
        arrivals.platform_name,
        arrivals.direction,
        arrivals.last_vehicle_id as vehicle_id,
        arrivals.arrival_time,
        arrivals.last_seen_at,
        stations.role,
        -- The same train's previous arrival on this board: a prediction
        -- polled before it belongs to that earlier visit.
        lag(arrivals.last_seen_at) over (
            partition by
                arrivals.request_path,
                arrivals.naptan_id,
                arrivals.line_id,
                arrivals.platform_name,
                arrivals.direction,
                arrivals.last_vehicle_id
            order by arrivals.arrival_time, arrivals.arrival_key
        ) as previous_visit_last_seen_at
    from {{ ref('int_inferred_arrivals') }} as arrivals
    inner join vehicle_id_lines
        on arrivals.line_id = vehicle_id_lines.line_id
    inner join stations
        on
            arrivals.line_id = stations.line_id
            and arrivals.naptan_id = stations.naptan_id
    where
        arrivals.last_vehicle_id is not null
        and arrivals.direction is not null
),

predictions as (
    select
        predictions.request_path,
        predictions.naptan_id,
        predictions.line_id,
        predictions.platform_name,
        predictions.direction,
        predictions.vehicle_id,
        predictions.poll_id,
        predictions.polled_at,
        predictions.expected_arrival,
        -- Countdown as of the poll, not TfL's stale time_to_station (D40).
        date_diff('millisecond', predictions.polled_at, predictions.expected_arrival) / 1e3
            as predicted_seconds
    from {{ ref('int_arrivals_directed') }} as predictions
    inner join vehicle_id_lines
        on predictions.line_id = vehicle_id_lines.line_id
    where
        predictions.dt >= date '{{ var("raw_start_date") }}'
        and predictions.vehicle_id is not null
        and predictions.direction is not null
),

candidates as (
    select
        arrivals.arrival_key,
        horizons.horizon_minutes,
        predictions.poll_id,
        predictions.polled_at,
        predictions.expected_arrival,
        predictions.predicted_seconds,
        row_number() over (
            partition by arrivals.arrival_key, horizons.horizon_minutes
            order by
                abs(predictions.predicted_seconds - horizons.horizon_minutes * 60),
                predictions.polled_at,
                predictions.expected_arrival
        ) as candidate_rank
    from arrivals
    cross join horizons
    inner join predictions
        on
            arrivals.request_path = predictions.request_path
            and arrivals.naptan_id = predictions.naptan_id
            and arrivals.line_id = predictions.line_id
            -- A board with no platform name is still one board (D28).
            and coalesce(arrivals.platform_name, '') = coalesce(predictions.platform_name, '')
            and arrivals.direction = predictions.direction
            and arrivals.vehicle_id = predictions.vehicle_id
            -- Before the last sighting, which defines the arrival time.
            and arrivals.last_seen_at > predictions.polled_at
            and (
                arrivals.previous_visit_last_seen_at is null
                or arrivals.previous_visit_last_seen_at < predictions.polled_at
            )
            -- The lookback counts from the horizon, so a train held for a
            -- long time after a 10-minute countdown is still matched as
            -- late, not dropped as unmatched.
            and predictions.polled_at
            >= date_add(
                'minute',
                -(horizons.horizon_minutes + {{ var('prediction_accuracy_lookback_minutes') }}),
                arrivals.arrival_time
            )
            and predictions.predicted_seconds
            >= horizons.horizon_minutes * 60
            - {{ var('prediction_accuracy_bucket_half_width_seconds') }}
            and predictions.predicted_seconds
            < horizons.horizon_minutes * 60
            + {{ var('prediction_accuracy_bucket_half_width_seconds') }}
),

matched as (
    select
        arrival_key,
        horizon_minutes,
        poll_id,
        polled_at,
        expected_arrival,
        predicted_seconds
    from candidates
    where candidate_rank = 1
),

scored as (
    select
        arrivals.arrival_key,
        arrivals.line_id,
        arrivals.naptan_id,
        arrivals.role,
        arrivals.platform_name,
        arrivals.direction,
        arrivals.vehicle_id,
        arrivals.arrival_time,
        horizons.horizon_minutes,
        matched.poll_id,
        matched.polled_at,
        matched.expected_arrival as predicted_arrival_time,
        matched.predicted_seconds,
        matched.arrival_key is not null as is_matched,
        date_diff('millisecond', matched.polled_at, arrivals.arrival_time) / 1e3
            as actual_seconds,
        {{ london_local('arrivals.arrival_time') }} as london_ts
    from arrivals
    cross join horizons
    left join matched
        on
            arrivals.arrival_key = matched.arrival_key
            and horizons.horizon_minutes = matched.horizon_minutes
)

select
    {{ row_key(['arrival_key', 'horizon_minutes']) }} as prediction_error_key,
    arrival_key,
    line_id,
    naptan_id,
    role,
    platform_name,
    direction,
    vehicle_id,
    arrival_time,
    horizon_minutes,
    is_matched,
    poll_id,
    polled_at,
    predicted_arrival_time,
    predicted_seconds,
    actual_seconds,
    -- Positive: the train came later than the countdown said.
    actual_seconds - predicted_seconds as signed_error_seconds,
    abs(actual_seconds - predicted_seconds) as abs_error_seconds,
    cast(london_ts as date) as london_date,
    date_trunc('week', cast(london_ts as date)) as week_start
from scored
