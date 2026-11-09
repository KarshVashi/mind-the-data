{{ config(materialized='table') }}

-- Gaps between consecutive inferred arrivals per line, measurement station
-- and direction (PLAN 6.2, 7.3). Platforms are pooled, because timetables
-- have no platforms and per-platform gaps at a two-platform station would
-- read as twice the scheduled ones (D29). Boards with no direction list
-- trains arriving to terminate, which have no timetable, and are left out
-- (week-one review, Q7). A gap belongs to the London hour of the arrival
-- that ends it, the train a waiting passenger boards (D30).
--
-- A gap that spans a collection gap (the station went more than
-- `collection_gap_seconds` between successful arrivals polls) is kept but
-- flagged: trains may have arrived unseen, so it must not read as a long
-- gap (D28).

with arrivals as (
    select
        arrivals.line_id,
        arrivals.naptan_id,
        arrivals.request_path,
        arrivals.direction,
        arrivals.arrival_time,
        arrivals.arrival_key
    from {{ ref('int_inferred_arrivals') }} as arrivals
    -- Only the lines each station is measured for; an interchange board
    -- also lists lines measured elsewhere.
    inner join {{ ref('seed_measurement_stations') }} as stations
        on
            arrivals.line_id = stations.line_id
            and arrivals.naptan_id = stations.naptan_id
    where arrivals.direction is not null
),

paired as (
    select
        *,
        lag(arrival_time) over (
            partition by line_id, naptan_id, request_path, direction
            order by arrival_time, arrival_key
        ) as previous_arrival_time
    from arrivals
),

polls as (
    select
        request_path,
        polled_at,
        lead(polled_at) over (
            partition by request_path order by polled_at, poll_id
        ) as next_polled_at
    from {{ ref('stg_polls') }}
    where
        feed = 'arrivals'
        and is_ok
        and dt >= date '{{ var("raw_start_date") }}'
),

collection_gaps as (
    -- One row per UTC date a gap touches, so headways join on an equality
    -- (station and date) and the join stays linear as history grows.
    select
        polls.request_path,
        polls.polled_at as gap_start,
        polls.next_polled_at as gap_end,
        gap_dates.gap_date
    from polls
    cross join
        unnest(
            sequence(
                cast(polls.polled_at as date), cast(polls.next_polled_at as date), interval '1' day
            )
        )
            as gap_dates (gap_date)
    where
        date_diff('millisecond', polls.polled_at, polls.next_polled_at)
        > {{ var('collection_gap_seconds') }} * 1000
),

headway_dates as (
    select
        paired.*,
        spanned.headway_date
    from paired
    cross join
        unnest(
            sequence(
                cast(paired.previous_arrival_time as date),
                cast(paired.arrival_time as date),
                interval '1' day
            )
        ) as spanned (headway_date)
    where paired.previous_arrival_time is not null
),

headways as (
    select
        paired.line_id,
        paired.naptan_id,
        paired.request_path,
        paired.direction,
        paired.previous_arrival_time,
        paired.arrival_time,
        paired.arrival_key,
        date_diff('millisecond', paired.previous_arrival_time, paired.arrival_time) / 1e3
            as headway_seconds,
        {{ london_local('paired.previous_arrival_time') }} as previous_london_ts,
        {{ london_local('paired.arrival_time') }} as london_ts,
        count(collection_gaps.gap_start) > 0 as spans_collection_gap
    from headway_dates as paired
    left join collection_gaps
        on
            paired.request_path = collection_gaps.request_path
            and paired.headway_date = collection_gaps.gap_date
            and paired.arrival_time > collection_gaps.gap_start
            and paired.previous_arrival_time < collection_gaps.gap_end
    group by
        paired.line_id,
        paired.naptan_id,
        paired.request_path,
        paired.direction,
        paired.previous_arrival_time,
        paired.arrival_time,
        paired.arrival_key
)

select
    line_id,
    naptan_id,
    request_path,
    direction,
    previous_arrival_time,
    arrival_time,
    headway_seconds,
    spans_collection_gap,
    previous_london_ts,
    london_ts,
    cast(london_ts as date) as london_date,
    arrival_key as headway_key,
    hour(london_ts) as london_hour,
    date_trunc('week', cast(london_ts as date)) as week_start
from headways
