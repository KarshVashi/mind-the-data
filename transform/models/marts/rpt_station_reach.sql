-- Tagged `exploratory`: the daily prod build excludes it (ADR-018).
{{ config(materialized='table', tags=['exploratory']) }}

-- Should I Run (ADR-018, D76): which trains at station A call at station B.
-- One row per line, direction, origin A, destination B and train
-- destination D (the `destinationNaptanId` an arrivals board lists), where
-- some route of the line's latest route sequence in that direction calls
-- at A, then B, then D, with D at or after B. A train listed for D
-- therefore calls at B, on at least one route. D is any stop at or after
-- B, not only the route's terminus, so a train turning short (Victoria to
-- Seven Sisters) is matched too.
--
-- On a line whose routes split after A (Northern via Bank or via Charing
-- Cross, Edgware or High Barnet), a destination served by routes both
-- through B and not through B is flagged `is_ambiguous`: the board's
-- destination alone cannot say whether that train calls at B. The page
-- shows such trains as "may not call at B", never as a train to take.
--
-- Lines that link by vehicle id only, as the run times exist only there.

with route_stops as (
    {{ latest_route_stops() }}
),

reach as (
    select
        origin.line_id,
        origin.direction,
        origin.naptan_id as origin_naptan_id,
        destination.naptan_id as destination_naptan_id,
        terminal.naptan_id as train_destination_naptan_id,
        origin.route_ordinal,
        destination.stop_sequence - origin.stop_sequence as stops_to_destination
    from route_stops as origin
    inner join route_stops as destination
        on
            origin.line_id = destination.line_id
            and origin.direction = destination.direction
            and origin.route_ordinal = destination.route_ordinal
            and origin.stop_sequence < destination.stop_sequence
    inner join route_stops as terminal
        on
            destination.line_id = terminal.line_id
            and destination.direction = terminal.direction
            and destination.route_ordinal = terminal.route_ordinal
            and destination.stop_sequence <= terminal.stop_sequence
    where origin.naptan_id <> destination.naptan_id
),

-- Routes from A that reach D without calling at B after A.
bypassing as (
    select distinct
        origin.line_id,
        origin.direction,
        origin.naptan_id as origin_naptan_id,
        terminal.naptan_id as train_destination_naptan_id,
        origin.route_ordinal
    from route_stops as origin
    inner join route_stops as terminal
        on
            origin.line_id = terminal.line_id
            and origin.direction = terminal.direction
            and origin.route_ordinal = terminal.route_ordinal
            and origin.stop_sequence < terminal.stop_sequence
),

grouped as (
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        train_destination_naptan_id,
        count(distinct route_ordinal) as routes,
        min(stops_to_destination) as stops_to_destination,
        array_agg(distinct route_ordinal) as route_ordinals
    from reach
    group by
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        train_destination_naptan_id
),

ambiguous as (
    select
        grouped.line_id,
        grouped.direction,
        grouped.origin_naptan_id,
        grouped.destination_naptan_id,
        grouped.train_destination_naptan_id
    from grouped
    inner join bypassing
        on
            grouped.line_id = bypassing.line_id
            and grouped.direction = bypassing.direction
            and grouped.origin_naptan_id = bypassing.origin_naptan_id
            and grouped.train_destination_naptan_id = bypassing.train_destination_naptan_id
            and not contains(grouped.route_ordinals, bypassing.route_ordinal)
    group by
        grouped.line_id,
        grouped.direction,
        grouped.origin_naptan_id,
        grouped.destination_naptan_id,
        grouped.train_destination_naptan_id
)

select
    {{ row_key([
        'grouped.line_id', 'grouped.direction', 'grouped.origin_naptan_id',
        'grouped.destination_naptan_id', 'grouped.train_destination_naptan_id',
    ]) }} as station_reach_key,
    grouped.line_id,
    grouped.direction,
    grouped.origin_naptan_id,
    origin_station.station_name as origin_name,
    grouped.destination_naptan_id,
    destination_station.station_name as destination_name,
    grouped.train_destination_naptan_id,
    train_destination_station.station_name as train_destination_name,
    grouped.routes,
    grouped.stops_to_destination,
    ambiguous.line_id is not null as is_ambiguous
from grouped
left join ambiguous
    on
        grouped.line_id = ambiguous.line_id
        and grouped.direction = ambiguous.direction
        and grouped.origin_naptan_id = ambiguous.origin_naptan_id
        and grouped.destination_naptan_id = ambiguous.destination_naptan_id
        and grouped.train_destination_naptan_id = ambiguous.train_destination_naptan_id
left join {{ ref('dim_station') }} as origin_station
    on grouped.origin_naptan_id = origin_station.naptan_id
left join {{ ref('dim_station') }} as destination_station
    on grouped.destination_naptan_id = destination_station.naptan_id
left join {{ ref('dim_station') }} as train_destination_station
    on grouped.train_destination_naptan_id = train_destination_station.naptan_id
