{#
  The M4 measured-view inference, shared by the measurement-station models
  (int_inferred_arrivals, int_vehicle_passes, int_headways, fed by the
  per-station arrivals feed) and the every-station models
  (int_station_inferred_arrivals, int_station_vehicle_passes,
  int_station_headways, fed by the per-line line_arrivals feed, ADR-016).
  One body, so both views apply the same rules; only the inputs and the
  window differ. With the measurement-station arguments, each macro renders
  the same SQL the models held before it was shared.
#}


{#
  Train arrivals inferred from successive arrival boards (PLAN 7.2). The SQL
  twin of src/mind_the_data/analysis/arrivals.py, which is the oracle: both
  must give the same arrivals on the same input (tests/unit/test_arrival_parity.py
  and the unit test int_inferred_arrivals_matches_the_reference). Each step
  below names the rule it mirrors; DECISIONS.md D25 to D28 and D40 explain
  the readings.

  Every comparison runs on integer milliseconds, as the reference does, so a
  value exactly on a tolerance falls the same side in both.

  A board is one request (station or line), stop, line, platform and
  direction; "the next poll" is the request's next successful poll.

  predictions: relation shaped like stg_arrivals. polls: relation shaped
  like stg_polls. *_where: SQL predicates bounding each read.
  output_where: optional predicate on the arrivals kept (expected_arrival
  is the arrival time).
#}
{% macro inferred_arrivals(predictions, polls, predictions_where, polls_where, output_where=none) %}
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
    from {{ polls }}
    where
        {{ polls_where }}
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
    from {{ predictions }} as arrivals
    inner join polls
        on
            arrivals.poll_id = polls.poll_id
            and arrivals.request_path = polls.request_path
    left join vehicle_id_lines
        on arrivals.line_id = vehicle_id_lines.line_id
    where {{ predictions_where }}
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
    {%- if output_where %}
        {{ output_where }}
        and (
            next_arrival_time is null
            or date_diff('millisecond', expected_arrival, next_arrival_time) > {{ match_ms }}
        )
    {%- else %}
        next_arrival_time is null
        or date_diff('millisecond', expected_arrival, next_arrival_time) > {{ match_ms }}
    {%- endif %}
{%- endmacro %}


{#
  Trains seen on the arrival boards, counted by vehicle id (ADR-014). A
  visit is a run of sightings of one vehicle id at one stop, line and
  direction, any platform, with no gap over `vehicle_pass_visit_gap_seconds`
  between them. A visit is a pass when the train came within
  `vehicle_pass_threshold_seconds` of the stop (time to go read from the
  poll, as D40). Its time is the expected arrival of its last sighting, the
  time the inference gives the same train (PLAN 7.2 rule 3).

  Nothing here links sightings by expected arrival, so it is independent of
  the inference's linking rules (D26, D27). Placeholder ids are no id, and
  sightings without an id are left out: the measured-hours models count
  those trains from the inference instead.

  output_where: optional predicate on the visits kept (pass_time is the
  pass).
#}
{% macro vehicle_passes(predictions, predictions_where, output_where=none) %}
with sightings as (
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        polled_at,
        expected_arrival,
        greatest(0, date_diff('second', polled_at, expected_arrival)) as seconds_to_arrival
    from {{ predictions }}
    where
        {{ predictions_where }}
        and direction is not null
        and vehicle_id is not null
        and vehicle_id not in (
            {%- for placeholder in var('placeholder_vehicle_ids') %}
                '{{ placeholder }}'{{ ',' if not loop.last }}
            {%- endfor %}
        )
),

gaps as (
    select
        *,
        lag(polled_at) over vehicle_sightings as previous_polled_at
    from sightings
    window vehicle_sightings as (
        partition by line_id, naptan_id, direction, vehicle_id
        order by polled_at, expected_arrival
    )
),

numbered as (
    select
        *,
        sum(
            if(
                previous_polled_at is null
                or date_diff('millisecond', previous_polled_at, polled_at)
                > {{ var('vehicle_pass_visit_gap_seconds') }} * 1000,
                1,
                0
            )
        ) over (
            partition by line_id, naptan_id, direction, vehicle_id
            order by polled_at, expected_arrival
            rows between unbounded preceding and current row
        ) as visit_number
    from gaps
),

visits as (
    select
        line_id,
        naptan_id,
        direction,
        vehicle_id,
        visit_number,
        min(polled_at) as first_seen_at,
        max(polled_at) as last_seen_at,
        count(*) as sightings,
        min(seconds_to_arrival) as min_seconds_to_arrival,
        max_by(expected_arrival, polled_at) as pass_time
    from numbered
    group by line_id, naptan_id, direction, vehicle_id, visit_number
)

select
    line_id,
    naptan_id,
    direction,
    vehicle_id,
    visit_number,
    first_seen_at,
    last_seen_at,
    sightings,
    min_seconds_to_arrival,
    pass_time,
    cast({{ london_local('pass_time') }} as date) as london_date,
    hour({{ london_local('pass_time') }}) as london_hour,
    {{ row_key(['line_id', 'naptan_id', 'direction', 'vehicle_id', 'first_seen_at']) }}
        as vehicle_pass_key
from visits
where
    min_seconds_to_arrival <= {{ var('vehicle_pass_threshold_seconds') }}
    {%- if output_where %}
        and {{ output_where }}
    {%- endif %}
{%- endmacro %}


{#
  Gaps between consecutive inferred arrivals per line, stop and direction
  (PLAN 6.2, 7.3). Platforms are pooled, because timetables have no
  platforms and per-platform gaps at a two-platform station would read as
  twice the scheduled ones (D29). Boards with no direction list trains
  arriving to terminate, which have no timetable, and are left out
  (week-one review, Q7). A gap belongs to the London hour of the arrival
  that ends it, the train a waiting passenger boards (D30).

  A gap that spans a collection gap (the request went more than
  `collection_gap_seconds` between successful polls) is kept but flagged:
  trains may have arrived unseen, so it must not read as a long gap (D28).

  stations: optional relation of (line_id, naptan_id) pairs to keep; an
  interchange board also lists lines measured elsewhere.
#}
{% macro headways(arrivals, polls, polls_where, stations=none) %}
with arrivals as (
    select
        arrivals.line_id,
        arrivals.naptan_id,
        arrivals.request_path,
        arrivals.direction,
        arrivals.arrival_time,
        arrivals.arrival_key
    from {{ arrivals }} as arrivals
    {%- if stations %}
        -- Only the lines each station is measured for; an interchange board
        -- also lists lines measured elsewhere.
        inner join {{ stations }} as stations
            on
                arrivals.line_id = stations.line_id
                and arrivals.naptan_id = stations.naptan_id
    {%- endif %}
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
    from {{ polls }}
    where
        {{ polls_where }}
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
{%- endmacro %}
