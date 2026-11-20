-- Tagged `exploratory`: the daily prod build excludes it (ADR-018).
{{ config(materialized='table', tags=['exploratory']) }}

-- Should I Run (ADR-018, D76): one row per train that passed station A and
-- then station B on the same line and direction, from the vehicle-id
-- passes (int_station_vehicle_passes, or int_vehicle_passes with
-- journey_source = measurement). Only lines that link by vehicle id
-- (seed_vehicle_id_lines, D27) are read, so not the District, DLR, tram,
-- Waterloo & City or the lines with trip ids.
--
-- A pair (A, B) is one where B comes after A on some route of the line's
-- latest route sequence in that direction; a train's pass at B upstream of
-- A, met on its next trip round, is never a run. For each pass at A, the
-- run is the vehicle's first pass at B after it, provided that pass is
-- within `journey_max_run_minutes` and comes before the vehicle's next
-- pass at A: an id seen at A twice with no B in between (a lost sighting
-- at B, or the id reused by another train) gives no run for the earlier
-- pass rather than a run stretched across two trips. A train on a branch
-- that does not call at B has no pass at B, so it has no run.
--
-- Only passes at A on weekdays (day_types) inside the slot windows, widened
-- by `journey_wait_lookahead_minutes` after each, so the slot model has the
-- train that ends the last wait of a window, are kept. A pass time is the expected arrival
-- at the train's last sighting (PLAN 7.2), so a run is arrival at A to
-- arrival at B: it includes the dwell at A, and each end carries about a
-- minute of board resolution.
--
-- Rebuilt in full over the last `journey_history_days`; it needs an
-- incremental, date-windowed build before it moves to prod.

with route_stops as (
    {{ latest_route_stops() }}
),

pairs as (
    -- Branches share stations, so a pair can come from several routes.
    select distinct
        origin.line_id,
        origin.direction,
        origin.naptan_id as origin_naptan_id,
        destination.naptan_id as destination_naptan_id
    from route_stops as origin
    inner join route_stops as destination
        on
            origin.line_id = destination.line_id
            and origin.direction = destination.direction
            and origin.route_ordinal = destination.route_ordinal
            and origin.stop_sequence < destination.stop_sequence
    where origin.naptan_id <> destination.naptan_id
),

passes as (
    select
        passes.line_id,
        passes.naptan_id,
        passes.direction,
        passes.vehicle_id,
        passes.pass_time,
        passes.london_date,
        {{ london_local('passes.pass_time') }} as london_ts,
        lead(passes.pass_time) over (
            partition by passes.line_id, passes.naptan_id, passes.direction, passes.vehicle_id
            order by passes.pass_time
        ) as next_pass_time
    from {{ journey_passes() }} as passes
    inner join {{ ref('seed_vehicle_id_lines') }} as linked
        on
            passes.line_id = linked.line_id
            and linked.link_by_vehicle_id
    where {{ journey_history_filter('passes.london_date') }}
),

origins as (
    select
        passes.*,
        hour(passes.london_ts) * 60 + minute(passes.london_ts) as london_minute
    from passes
    inner join {{ ref('day_types') }} as day_types
        on day_of_week(passes.london_date) = day_types.iso_day_of_week
    where
        day_types.day_class = 'weekday'
        and {{ in_journey_windows(
            'hour(passes.london_ts) * 60 + minute(passes.london_ts)',
            var('journey_wait_lookahead_minutes'),
        ) }}
),

candidates as (
    select
        origins.line_id,
        origins.direction,
        origins.vehicle_id,
        origins.naptan_id as origin_naptan_id,
        pairs.destination_naptan_id,
        origins.pass_time as origin_pass_time,
        origins.london_date,
        origins.london_ts as origin_london_ts,
        origins.london_minute as origin_london_minute,
        destinations.pass_time as destination_pass_time,
        destinations.london_ts as destination_london_ts
    from origins
    inner join pairs
        on
            origins.line_id = pairs.line_id
            and origins.direction = pairs.direction
            and origins.naptan_id = pairs.origin_naptan_id
    inner join passes as destinations
        on
            pairs.line_id = destinations.line_id
            and pairs.direction = destinations.direction
            and pairs.destination_naptan_id = destinations.naptan_id
            and origins.vehicle_id = destinations.vehicle_id
            and origins.pass_time < destinations.pass_time
            and destinations.pass_time
            <= origins.pass_time + interval '{{ var("journey_max_run_minutes") }}' minute
            and (
                origins.next_pass_time is null
                or origins.next_pass_time > destinations.pass_time
            )
),

runs as (
    select
        line_id,
        direction,
        vehicle_id,
        origin_naptan_id,
        destination_naptan_id,
        origin_pass_time,
        london_date,
        origin_london_ts,
        origin_london_minute,
        min(destination_pass_time) as destination_pass_time,
        min(destination_london_ts) as destination_london_ts
    from candidates
    group by
        line_id,
        direction,
        vehicle_id,
        origin_naptan_id,
        destination_naptan_id,
        origin_pass_time,
        london_date,
        origin_london_ts,
        origin_london_minute
)

select
    {{ row_key([
        'line_id', 'direction', 'vehicle_id', 'origin_naptan_id', 'destination_naptan_id',
        'origin_pass_time',
    ]) }} as vehicle_run_key,
    line_id,
    direction,
    vehicle_id,
    origin_naptan_id,
    destination_naptan_id,
    origin_pass_time,
    destination_pass_time,
    date_diff('second', origin_pass_time, destination_pass_time) as run_seconds,
    london_date,
    origin_london_ts,
    origin_london_minute,
    hour(origin_london_ts) as origin_london_hour,
    cast(destination_london_ts as date) as destination_london_date,
    hour(destination_london_ts) as destination_london_hour
from runs
