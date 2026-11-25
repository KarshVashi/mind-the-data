{{
    config(
        tags=['station'],
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='board_dropout_key',
        partitioned_by=['london_date'],
        on_schema_change='fail',
        incremental_predicates=window_incremental_predicates('london_date'),
        pre_hook="{{ delete_window_rows('london_date') }}",
    )
}}

-- Did station X's boards show a train that demonstrably passed it (D75,
-- pending)? One row per train judged at X: a vehicle whose consecutive
-- passes (int_station_vehicle_passes) run A, X, C along one of the line's
-- routes was shown at X; one whose consecutive passes run A then C, where
-- the route has exactly one station X between them, is a candidate
-- dropout at X. "Shown" means the train came within
-- `vehicle_pass_threshold_seconds` of X on X's boards.
--
-- A candidate is checked against X's own listings (int_station_arrivals_
-- directed) from 10 minutes before the pass at A to 2 minutes after the
-- pass at C, in the trip's direction (A's and C's when they agree; any
-- when they do not) or with none. If X listed the train
-- within the threshold, it was shown after all: a lingering last
-- prediction put a pass out of order, or the listing had no direction
-- (D65 could not fill it), so no pass was made. Only a
-- train X never listed in that window, or listed but never within the
-- threshold, is dropped; `dropout_kind` says which.
--
-- A row is judged (`exclusion_reason` null) unless:
-- - line_not_measured: seed_board_dropout_lines leaves the line out;
-- - collection_gap: the line feed went more than `collection_gap_seconds`
--   between successful polls from 10 minutes before the pass at A to 2
--   minutes after the pass at C (D28). X's boards were not read, and
--   missing data is never a verdict on them;
-- - terminus: X is the first or last stop of one of the line's routes, or a
--   seeded terminus (the rule of D66 and D70), where boards list trains
--   twice or without a direction;
-- - dropout_undetectable: a shown train whose stations either side are next
--   to each other on another route, or have another station between them
--   on another route (Euston, Mornington Crescent, Camden Town). A train
--   seen at A then C there may have taken the other branch, so a dropout at
--   X could never be seen, and counting only its shown trains would bias
--   the share to 0;
-- - timetabled_run_through: some TfL timetable stopping pattern on the line
--   calls at A and then C, so a train may run through X by design (fast
--   Metropolitan trains, the Piccadilly at Turnham Green). Shown and dropped
--   trains are both left out, so the share is not biased either way;
-- - span_too_long: the passes at A and C are more than
--   `board_dropout_max_span_minutes` apart, so the train may have been held,
--   stabled or reversed.
--
-- A pair counts only when A and C are two stops apart on every route that
-- has both, so a train missed by two boards in a row is not judged, nor is
-- one missed by every board: this is a floor on what the boards drop, not
-- a count.
--
-- Rebuilt by window like int_station_vehicle_passes (macros/
-- window_rebuild.sql): each run recomputes the London dates of the window,
-- reading a day either side of passes, listings and polls so a train
-- judged near midnight still has its passes at A and C. Routes and
-- timetables are read as fetched by the window's last day.

{%- set bounds = window_rebuild_bounds() %}

with lines as (
    select
        line_id,
        is_measured
    from {{ ref('seed_board_dropout_lines') }}
),

latest_route_fetches as (
    -- The latest route sequences of each line and direction fetched by the
    -- window's last day, as dim_station reads them. Pinned to the window,
    -- like the timetables below, so rebuilding a past day later gives it
    -- the same routes and exclusions.
    select
        line_id,
        direction,
        max_by(poll_id, polled_at) as poll_id,
        max_by(request_path, polled_at) as request_path
    from {{ ref('stg_route_sequence') }}
    where dt between date '{{ var("raw_start_date") }}' and date '{{ bounds.end }}'
    group by line_id, direction
),

route_stops as (
    select
        route_stops.line_id,
        route_stops.naptan_id,
        route_stops.stop_sequence,
        concat(
            route_stops.poll_id,
            route_stops.request_path,
            cast(route_stops.route_ordinal as varchar)
        ) as route_key,
        lead(route_stops.naptan_id, 1) over route_order as next_naptan_id,
        lead(route_stops.naptan_id, 2) over route_order as second_naptan_id,
        max(route_stops.stop_sequence) over (
            partition by route_stops.poll_id, route_stops.request_path, route_stops.route_ordinal
        ) as last_stop_sequence
    from {{ ref('stg_route_sequence') }} as route_stops
    inner join latest_route_fetches
        on
            route_stops.poll_id = latest_route_fetches.poll_id
            and route_stops.request_path = latest_route_fetches.request_path
    where route_stops.dt between date '{{ var("raw_start_date") }}' and date '{{ bounds.end }}'
    window route_order as (
        partition by route_stops.poll_id, route_stops.request_path, route_stops.route_ordinal
        order by route_stops.stop_sequence
    )
),

route_triples as (
    select distinct
        line_id,
        naptan_id as previous_naptan_id,
        next_naptan_id as naptan_id,
        second_naptan_id as next_naptan_id
    from route_stops
    where second_naptan_id is not null
),

adjacent_pairs as (
    -- Both ways round: stations next to each other on any route.
    select distinct
        line_id,
        naptan_id as from_naptan_id,
        next_naptan_id as to_naptan_id
    from route_stops
    where next_naptan_id is not null
    union
    select distinct
        line_id,
        next_naptan_id as from_naptan_id,
        naptan_id as to_naptan_id
    from route_stops
    where next_naptan_id is not null
),

distant_pairs as (
    -- Stations three or more stops apart on some route, in route order.
    select distinct
        from_stops.line_id,
        from_stops.naptan_id as from_naptan_id,
        to_stops.naptan_id as to_naptan_id
    from route_stops as from_stops
    inner join route_stops as to_stops
        on
            from_stops.route_key = to_stops.route_key
            and to_stops.stop_sequence >= from_stops.stop_sequence + 3
),

skip_pairs as (
    -- Two stations with exactly one station between them on some route,
    -- the same one on every route, and neither next to each other nor
    -- further apart on any route: a train seen at one then the other has
    -- passed that station, and only that one.
    select
        route_triples.line_id,
        route_triples.previous_naptan_id,
        route_triples.next_naptan_id,
        min(route_triples.naptan_id) as naptan_id
    from route_triples
    left join adjacent_pairs
        on
            route_triples.line_id = adjacent_pairs.line_id
            and route_triples.previous_naptan_id = adjacent_pairs.from_naptan_id
            and route_triples.next_naptan_id = adjacent_pairs.to_naptan_id
    left join distant_pairs
        on
            route_triples.line_id = distant_pairs.line_id
            and route_triples.previous_naptan_id = distant_pairs.from_naptan_id
            and route_triples.next_naptan_id = distant_pairs.to_naptan_id
    where adjacent_pairs.line_id is null and distant_pairs.line_id is null
    group by
        route_triples.line_id,
        route_triples.previous_naptan_id,
        route_triples.next_naptan_id
    having count(distinct route_triples.naptan_id) = 1
),

termini as (
    select distinct
        line_id,
        naptan_id
    from route_stops
    where stop_sequence = 1 or stop_sequence = last_stop_sequence
    union
    select distinct
        line_id,
        naptan_id
    from {{ ref('seed_measurement_stations') }}
    where role = 'terminus'
),

pattern_calls as (
    select
        line_id,
        stop_id as to_naptan_id,
        coalesce(
            lag(stop_id) over (
                partition by poll_id, request_path, route_ordinal, interval_ordinal
                order by stop_ordinal
            ),
            departure_stop_id
        ) as from_naptan_id
    from {{ ref('stg_timetable_stop_intervals') }}
    where dt between date '{{ var("raw_start_date") }}' and date '{{ bounds.end }}'
),

timetabled_pairs as (
    -- Consecutive calls of any stopping pattern TfL has timetabled.
    select distinct
        line_id,
        from_naptan_id,
        to_naptan_id
    from pattern_calls
),

passes as (
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        pass_time,
        vehicle_pass_key,
        lag(naptan_id) over vehicle_order as previous_pass_naptan_id
    from {{ ref('int_station_vehicle_passes') }}
    where
        london_date
        between date '{{ bounds.read_start }}' and date '{{ bounds.end }}' + interval '1' day
    window vehicle_order as (
        partition by line_id, vehicle_id
        order by pass_time, vehicle_pass_key
    )
),

calls as (
    -- One call per station visit: a second pass at the same station right
    -- after the first (a board listing the train under both directions, or
    -- a long dwell split into two visits) adds nothing.
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        pass_time,
        vehicle_pass_key,
        lead(naptan_id, 1) over vehicle_order as next_naptan_id,
        lead(direction, 1) over vehicle_order as next_direction,
        lead(pass_time, 1) over vehicle_order as next_pass_time,
        lead(naptan_id, 2) over vehicle_order as second_naptan_id,
        lead(direction, 2) over vehicle_order as second_direction,
        lead(pass_time, 2) over vehicle_order as second_pass_time
    from passes
    where previous_pass_naptan_id is null or previous_pass_naptan_id != naptan_id
    window vehicle_order as (
        partition by line_id, vehicle_id
        order by pass_time, vehicle_pass_key
    )
),

shown as (
    select
        calls.line_id,
        calls.vehicle_id,
        calls.vehicle_pass_key,
        calls.naptan_id as previous_naptan_id,
        calls.next_naptan_id as naptan_id,
        calls.second_naptan_id as next_naptan_id,
        calls.pass_time as previous_pass_time,
        calls.next_pass_time as pass_time,
        calls.second_pass_time as next_pass_time,
        calls.direction as previous_direction,
        calls.second_direction as next_direction,
        skip_pairs.line_id is not null as is_detectable
    from calls
    inner join route_triples
        on
            calls.line_id = route_triples.line_id
            and calls.naptan_id = route_triples.previous_naptan_id
            and calls.next_naptan_id = route_triples.naptan_id
            and calls.second_naptan_id = route_triples.next_naptan_id
    left join skip_pairs
        on
            calls.line_id = skip_pairs.line_id
            and calls.naptan_id = skip_pairs.previous_naptan_id
            and calls.second_naptan_id = skip_pairs.next_naptan_id
            and calls.next_naptan_id = skip_pairs.naptan_id
),

candidates as (
    select
        calls.line_id,
        calls.vehicle_id,
        calls.vehicle_pass_key,
        calls.naptan_id as previous_naptan_id,
        skip_pairs.naptan_id,
        calls.next_naptan_id,
        calls.pass_time as previous_pass_time,
        calls.next_pass_time,
        calls.direction as previous_direction,
        calls.next_direction
    from calls
    inner join skip_pairs
        on
            calls.line_id = skip_pairs.line_id
            and calls.naptan_id = skip_pairs.previous_naptan_id
            and calls.next_naptan_id = skip_pairs.next_naptan_id
),

candidate_listings as (
    -- What X's boards listed for the candidate's vehicle on this trip: from
    -- 10 minutes before the pass at A (a board can hold a departed train for
    -- minutes, so A's pass time can come late) to 2 minutes after the pass
    -- at C, in the trip's direction or with none. The direction keeps out
    -- the same train's previous trip the other way, a few minutes earlier
    -- when A is near a terminus.
    select
        candidates.vehicle_pass_key,
        count(listings.vehicle_id) as x_listings,
        min(greatest(0, date_diff('second', listings.polled_at, listings.expected_arrival)))
            as x_min_seconds_to_arrival,
        max_by(listings.expected_arrival, listings.polled_at) as x_last_expected_arrival
    from candidates
    left join {{ ref('int_station_arrivals_directed') }} as listings
        on
            candidates.line_id = listings.line_id
            and candidates.naptan_id = listings.naptan_id
            and candidates.vehicle_id = listings.vehicle_id
            and listings.polled_at
            between candidates.previous_pass_time - interval '10' minute
            and candidates.next_pass_time + interval '2' minute
            and (
                listings.direction is null
                or candidates.previous_direction is null
                or candidates.previous_direction != candidates.next_direction
                or candidates.previous_direction = listings.direction
            )
            and listings.dt
            between date '{{ bounds.read_start }}' and date '{{ bounds.end }}' + interval '1' day
    group by candidates.vehicle_pass_key
),

line_polls as (
    select
        request_path,
        polled_at,
        lead(polled_at) over (partition by request_path order by polled_at, poll_id)
            as next_polled_at
    from {{ ref('stg_line_arrival_polls') }}
    where
        is_ok
        and dt between date '{{ bounds.read_start }}' and date '{{ bounds.end }}' + interval '1' day
),

collection_gaps as (
    -- The line feed went longer than `collection_gap_seconds` between
    -- successful polls (D28): X's boards were not read, so nothing missing
    -- from them in that time is the boards' doing.
    select
        request_path,
        polled_at as gap_start,
        next_polled_at as gap_end
    from line_polls
    where
        date_diff('millisecond', polled_at, next_polled_at)
        > {{ var('collection_gap_seconds') }} * 1000
),

checked as (
    select
        candidates.*,
        candidate_listings.x_listings,
        candidate_listings.x_min_seconds_to_arrival,
        candidate_listings.x_last_expected_arrival,
        coalesce(
            candidate_listings.x_min_seconds_to_arrival
            <= {{ var('vehicle_pass_threshold_seconds') }},
            false
        ) as is_listed_arriving
    from candidates
    inner join candidate_listings
        on candidates.vehicle_pass_key = candidate_listings.vehicle_pass_key
),

unchecked as (
    select
        line_id,
        vehicle_id,
        vehicle_pass_key,
        previous_naptan_id,
        naptan_id,
        next_naptan_id,
        previous_pass_time,
        pass_time,
        next_pass_time,
        previous_direction,
        next_direction,
        is_detectable,
        'shown' as board_outcome,
        cast(null as varchar) as dropout_kind
    from shown
    union all
    select
        line_id,
        vehicle_id,
        vehicle_pass_key,
        previous_naptan_id,
        naptan_id,
        next_naptan_id,
        previous_pass_time,
        if(is_listed_arriving, x_last_expected_arrival) as pass_time,
        next_pass_time,
        previous_direction,
        next_direction,
        true as is_detectable,
        if(is_listed_arriving, 'shown', 'dropped') as board_outcome,
        case
            when is_listed_arriving then null
            when x_listings > 0 then 'listed_not_arriving'
            else 'never_listed'
        end as dropout_kind
    from checked
),

judged as (
    -- Every row is checked for a collection gap, shown and dropped alike,
    -- so leaving them out does not bias the share.
    select
        unchecked.*,
        date_diff('millisecond', unchecked.previous_pass_time, unchecked.next_pass_time) / 1e3
            as span_seconds,
        count(collection_gaps.gap_start) > 0 as spans_collection_gap
    from unchecked
    left join collection_gaps
        on
            '/Line/' || unchecked.line_id || '/Arrivals' = collection_gaps.request_path
            and collection_gaps.gap_start < unchecked.next_pass_time + interval '2' minute
            and collection_gaps.gap_end > unchecked.previous_pass_time - interval '10' minute
    group by
        unchecked.line_id,
        unchecked.vehicle_id,
        unchecked.vehicle_pass_key,
        unchecked.previous_naptan_id,
        unchecked.naptan_id,
        unchecked.next_naptan_id,
        unchecked.previous_pass_time,
        unchecked.pass_time,
        unchecked.next_pass_time,
        unchecked.previous_direction,
        unchecked.next_direction,
        unchecked.is_detectable,
        unchecked.board_outcome,
        unchecked.dropout_kind
),

classified as (
    select
        judged.*,
        case
            when coalesce(lines.is_measured, false) = false then 'line_not_measured'
            when judged.spans_collection_gap then 'collection_gap'
            when termini.naptan_id is not null then 'terminus'
            when not judged.is_detectable then 'dropout_undetectable'
            when timetabled_pairs.line_id is not null then 'timetabled_run_through'
            when judged.span_seconds > {{ var('board_dropout_max_span_minutes') }} * 60
                then 'span_too_long'
        end as exclusion_reason,
        -- The pass at X, or halfway between A and C when there is none or a
        -- listing's last prediction falls outside A to C.
        if(
            judged.pass_time between judged.previous_pass_time and judged.next_pass_time,
            judged.pass_time,
            date_add(
                'millisecond', cast(judged.span_seconds * 500 as bigint), judged.previous_pass_time
            )
        ) as estimated_pass_time
    from judged
    left join lines
        on judged.line_id = lines.line_id
    left join termini
        on
            judged.line_id = termini.line_id
            and judged.naptan_id = termini.naptan_id
    left join timetabled_pairs
        on
            judged.line_id = timetabled_pairs.line_id
            and judged.previous_naptan_id = timetabled_pairs.from_naptan_id
            and judged.next_naptan_id = timetabled_pairs.to_naptan_id
)

select
    line_id,
    naptan_id,
    previous_naptan_id,
    next_naptan_id,
    vehicle_id,
    -- The boards either side agree on the direction, or it is unknown.
    if(previous_direction = next_direction, previous_direction) as direction,
    previous_pass_time,
    pass_time,
    next_pass_time,
    estimated_pass_time,
    span_seconds,
    board_outcome,
    dropout_kind,
    exclusion_reason,
    exclusion_reason is null as is_judged,
    board_outcome = 'dropped' as is_dropped,
    cast({{ london_local('estimated_pass_time') }} as date) as london_date,
    hour({{ london_local('estimated_pass_time') }}) as london_hour,
    date_trunc('week', cast({{ london_local('estimated_pass_time') }} as date)) as week_start,
    {{ row_key(['line_id', 'vehicle_pass_key']) }} as board_dropout_key
from classified
where
    cast({{ london_local('estimated_pass_time') }} as date)
    between date '{{ bounds.start }}' and date '{{ bounds.end }}'
