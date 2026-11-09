{{
    config(
        materialized='table',
    )
}}

-- Train arrivals inferred from successive arrival boards (PLAN 7.2). The SQL
-- twin of src/mind_the_data/analysis/arrivals.py, which is the oracle: both
-- must give the same arrivals on the same input (tests/unit/test_arrival_parity.py
-- and the unit test int_inferred_arrivals_matches_the_reference). Each step
-- below names the rule it mirrors; DECISIONS.md D25 to D28 and D40 explain
-- the readings.
--
-- Every comparison runs on integer milliseconds, as the reference does, so a
-- value exactly on a tolerance falls the same side in both.
--
-- Rebuilt in full each run, like the other intermediate models (ADR-012,
-- D24; DECISIONS.md D41): an arrival depends only on its poll and the
-- station's next successful poll, but an arrival at the end of the input is
-- provisional, and a merge could not remove one that a later poll turns
-- into a moving train.

{%- set match_ms = 45000 %}
{%- set duplicate_ms = 15000 %}
{%- set arrival_threshold_s = 120 %}

with polls as (
    -- Every successful arrivals poll of each station, empty boards included:
    -- absence from the station's next successful poll is what ends a train.
    select
        poll_id,
        request_path,
        polled_at,
        lead(poll_id) over station_polls as next_poll_id,
        lead(polled_at) over station_polls as next_polled_at
    from {{ ref('stg_polls') }}
    where
        feed = 'arrivals'
        and is_ok
        and dt >= date '{{ var("raw_start_date") }}'
    window station_polls as (partition by request_path order by polled_at, poll_id)
),

vehicle_id_lines as (
    select line_id
    from {{ ref('seed_vehicle_id_lines') }}
    where link_by_vehicle_id
),

predictions as (
    select
        arrivals.poll_id,
        arrivals.request_path,
        arrivals.naptan_id,
        arrivals.line_id,
        arrivals.platform_name,
        arrivals.direction,
        arrivals.prediction_id,
        arrivals.vehicle_id as listed_vehicle_id,
        arrivals.expected_arrival,
        arrivals.time_to_station,
        polls.polled_at,
        polls.next_poll_id,
        polls.next_polled_at,
        -- Placeholder ids are shared by several trains: no id (D27).
        case
            when
                arrivals.vehicle_id in (
                    {%- for placeholder in var('placeholder_vehicle_ids') %}
                        '{{ placeholder }}'{{ ',' if not loop.last }}
                    {%- endfor %}
                )
                then null
            else arrivals.vehicle_id
        end as vehicle_id,
        vehicle_id_lines.line_id is not null as links_by_vehicle_id,
        -- Time to go as of the poll, not TfL's stale time_to_station (D40).
        greatest(0, date_diff('second', polls.polled_at, arrivals.expected_arrival))
            as seconds_to_arrival,
        {{ row_key([
            'arrivals.request_path',
            'arrivals.naptan_id',
            'arrivals.line_id',
            'arrivals.platform_name',
            'arrivals.direction',
        ]) }} as board_key
    from {{ ref('stg_arrivals') }} as arrivals
    inner join polls
        on
            arrivals.poll_id = polls.poll_id
            and arrivals.request_path = polls.request_path
    left join vehicle_id_lines
        on arrivals.line_id = vehicle_id_lines.line_id
    where arrivals.dt >= date '{{ var("raw_start_date") }}'
),

listed as (
    -- Step 2 of the reference, duplicates: within one board in one poll, a
    -- prediction within 15 s of the row before it is the same train (D25).
    select
        *,
        lag(expected_arrival) over (
            partition by board_key, poll_id
            order by
                expected_arrival,
                prediction_id,
                coalesce(listed_vehicle_id, ''),
                time_to_station
        ) as previous_expected_arrival
    from predictions
),

sightings as (
    select *
    from listed
    where
        previous_expected_arrival is null
        or date_diff('millisecond', previous_expected_arrival, expected_arrival)
        > {{ duplicate_ms }}
),

candidates as (
    -- Step 3, linking: a sighting and one on the same board in the
    -- station's next successful poll, with how far apart they are as one
    -- train. Null distance means they cannot be the same train.
    select
        earlier.board_key,
        earlier.poll_id as earlier_poll_id,
        earlier.expected_arrival as earlier_expected_arrival,
        earlier.prediction_id as earlier_prediction_id,
        later.poll_id as later_poll_id,
        later.expected_arrival as later_expected_arrival,
        later.prediction_id as later_prediction_id,
        case
            when
                earlier.links_by_vehicle_id
                and earlier.vehicle_id is not null
                and later.vehicle_id is not null
                then
                    case
                        when
                            earlier.vehicle_id = later.vehicle_id
                            and abs(
                                date_diff(
                                    'millisecond', earlier.expected_arrival, later.expected_arrival
                                )
                            )
                            <= date_diff('millisecond', earlier.polled_at, later.polled_at)
                            + {{ match_ms }}
                            then 0
                    end
            when
                abs(date_diff('millisecond', earlier.expected_arrival, later.expected_arrival))
                <= {{ match_ms }}
                then abs(date_diff('millisecond', earlier.expected_arrival, later.expected_arrival))
            -- Standing at the platform: ranks after every moving match (D26).
            when
                later.seconds_to_arrival = 0
                and earlier.seconds_to_arrival * 1000 <= {{ match_ms }}
                and abs(
                    date_diff('millisecond', earlier.expected_arrival, later.expected_arrival)
                    - date_diff('millisecond', earlier.polled_at, later.polled_at)
                ) <= {{ match_ms }}
                then {{ match_ms }} + abs(
                    date_diff('millisecond', earlier.expected_arrival, later.expected_arrival)
                    - date_diff('millisecond', earlier.polled_at, later.polled_at)
                )
        end as distance_ms
    from sightings as earlier
    inner join sightings as later
        on
            earlier.board_key = later.board_key
            and earlier.next_poll_id = later.poll_id
),

ranked_candidates as (
    -- Each side's nearest candidate; ties to the earlier expected arrival,
    -- then the lower prediction id. Within one board and poll, sightings are
    -- more than 15 s apart, so the order is total.
    select
        *,
        row_number() over (
            partition by board_key, earlier_poll_id, earlier_expected_arrival
            order by distance_ms, later_expected_arrival, later_prediction_id
        ) as later_rank,
        row_number() over (
            partition by board_key, later_poll_id, later_expected_arrival
            order by distance_ms, earlier_expected_arrival, earlier_prediction_id
        ) as earlier_rank
    from candidates
    where distance_ms is not null
),

links as (
    -- A link only when the choice is mutual, so links are one to one.
    select
        board_key,
        earlier_poll_id,
        earlier_expected_arrival
    from ranked_candidates
    where later_rank = 1 and earlier_rank = 1
),

last_sightings as (
    -- Step 4, arrival: a sighting no link carries on is a train's last.
    -- With a next poll, it arrived if it was at most 120 s out; at the end
    -- of the input, only if it was due (0 s to go).
    select sightings.*
    from sightings
    left join links
        on
            sightings.board_key = links.board_key
            and sightings.poll_id = links.earlier_poll_id
            and sightings.expected_arrival = links.earlier_expected_arrival
    where
        links.board_key is null
        and (
            (
                sightings.next_poll_id is not null
                and sightings.seconds_to_arrival <= {{ arrival_threshold_s }}
            )
            or (sightings.next_poll_id is null and sightings.seconds_to_arrival = 0)
        )
),

arrivals as (
    select
        *,
        -- Step 5, repeats: an arrival followed within 45 s by another on the
        -- board is a train that flickered off the board; keep the later.
        lead(expected_arrival) over (
            partition by board_key
            order by expected_arrival, polled_at, prediction_id
        ) as next_arrival_time
    from last_sightings
)

select
    request_path,
    naptan_id,
    line_id,
    platform_name,
    direction,
    expected_arrival as arrival_time,
    case
        when seconds_to_arrival = 0 then 'at_platform' else 'left_board'
    end as rule,  -- noqa: RF04
    poll_id as last_poll_id,
    prediction_id as last_prediction_id,
    seconds_to_arrival as last_seconds_to_arrival,
    time_to_station as last_time_to_station,
    vehicle_id as last_vehicle_id,
    links_by_vehicle_id,
    polled_at as last_seen_at,
    next_polled_at as confirmed_at,
    board_key,
    {{ row_key(['board_key', 'poll_id', 'expected_arrival']) }} as arrival_key,
    cast(expected_arrival as date) as dt
from arrivals
where
    next_arrival_time is null
    or date_diff('millisecond', expected_arrival, next_arrival_time) > {{ match_ms }}
