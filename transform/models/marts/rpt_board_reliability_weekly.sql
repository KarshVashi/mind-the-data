{{ config(materialized='table', tags=['station']) }}

-- Can you trust TfL's boards? Per ISO week (London), line and station: how
-- many trains that demonstrably passed the station its boards showed, and
-- how many they dropped (int_board_dropouts, D75, pending), and of those
-- how many the station never listed at all. Line columns
-- repeat on each of the line's rows: the line's dropout share, and the
-- share of the measured view's long gaps at these stations that a dropped
-- train sits inside, the test D68 reports beside the Honesty Gap. The
-- `line_measurement_` columns repeat the long-gap count on D68's own
-- ground, the core and branch measurement stations, so it can be read
-- like for like beside the Honesty Gap; they are not limited to Good
-- Service hours.
--
-- Only judged trains count; a station with none in a week has no row.
-- Shares are null, never 0, below the minimum n: `board_dropout_min_trains`
-- judged trains for a dropout share, `board_dropout_min_long_gaps` long
-- gaps for the long-gap share. Not published.
--
-- A long gap counts when the measured view counts it: long (D34), in a
-- scheduled hour that passed the quality gate (is_in_band). It contains a
-- dropout when a train dropped at the same station, in the same direction,
-- has its estimated pass time (halfway between the stations either side)
-- inside the gap. A dropout with no agreed direction matches no gap.

with judged as (
    select *
    from {{ ref('int_board_dropouts') }}
    where is_judged
),

station_trains as (
    select
        week_start,
        line_id,
        naptan_id,
        count(*) as trains_judged,
        count_if(is_dropped) as trains_dropped,
        count_if(dropout_kind = 'never_listed') as trains_never_listed
    from judged
    group by week_start, line_id, naptan_id
),

long_gaps as (
    select
        headways.headway_key,
        headways.week_start,
        headways.line_id,
        headways.naptan_id,
        headways.direction,
        headways.previous_arrival_time,
        headways.arrival_time
    from {{ ref('int_station_service_headways') }} as headways
    inner join station_trains
        on
            headways.week_start = station_trains.week_start
            and headways.line_id = station_trains.line_id
            and headways.naptan_id = station_trains.naptan_id
    where headways.is_long_gap and headways.is_in_band and headways.is_scheduled
),

long_gaps_flagged as (
    select
        long_gaps.week_start,
        long_gaps.line_id,
        long_gaps.naptan_id,
        long_gaps.headway_key,
        count(judged.board_dropout_key) > 0 as contains_dropout
    from long_gaps
    left join judged
        on
            long_gaps.line_id = judged.line_id
            and long_gaps.naptan_id = judged.naptan_id
            and long_gaps.direction = judged.direction
            and judged.is_dropped
            and long_gaps.previous_arrival_time < judged.estimated_pass_time
            and long_gaps.arrival_time > judged.estimated_pass_time
    group by long_gaps.week_start, long_gaps.line_id, long_gaps.naptan_id, long_gaps.headway_key
),

station_gaps as (
    select
        week_start,
        line_id,
        naptan_id,
        count(*) as long_gaps,
        count_if(contains_dropout) as long_gaps_with_dropout
    from long_gaps_flagged
    group by week_start, line_id, naptan_id
),

measurement_stations as (
    -- The Honesty Gap's ground (D68): core and branch measurement stations.
    select distinct
        line_id,
        naptan_id
    from {{ ref('seed_measurement_stations') }}
    where role in ('core', 'branch')
),

stations as (
    select
        station_trains.week_start,
        station_trains.line_id,
        station_trains.naptan_id,
        station_trains.trains_judged,
        station_trains.trains_dropped,
        station_trains.trains_never_listed,
        coalesce(station_gaps.long_gaps, 0) as long_gaps,
        coalesce(station_gaps.long_gaps_with_dropout, 0) as long_gaps_with_dropout,
        measurement_stations.naptan_id is not null as is_measurement_station
    from station_trains
    left join measurement_stations
        on
            station_trains.line_id = measurement_stations.line_id
            and station_trains.naptan_id = measurement_stations.naptan_id
    left join station_gaps
        on
            station_trains.week_start = station_gaps.week_start
            and station_trains.line_id = station_gaps.line_id
            and station_trains.naptan_id = station_gaps.naptan_id
),

with_line as (
    select
        *,
        sum(trains_judged) over line_week as line_trains_judged,
        sum(trains_dropped) over line_week as line_trains_dropped,
        sum(trains_never_listed) over line_week as line_trains_never_listed,
        sum(long_gaps) over line_week as line_long_gaps,
        sum(long_gaps_with_dropout) over line_week as line_long_gaps_with_dropout,
        sum(if(is_measurement_station, long_gaps, 0)) over line_week
            as line_measurement_long_gaps,
        sum(if(is_measurement_station, long_gaps_with_dropout, 0)) over line_week
            as line_measurement_long_gaps_with_dropout
    from stations
    window line_week as (partition by week_start, line_id)
)

select
    with_line.week_start,
    with_line.line_id,
    with_line.naptan_id,
    dim_station.station_name,
    dropout_lines.caveat as line_caveat,
    with_line.trains_judged,
    with_line.trains_dropped,
    with_line.trains_never_listed,
    if(
        with_line.trains_judged >= {{ var('board_dropout_min_trains') }},
        1e0 * with_line.trains_dropped / with_line.trains_judged
    ) as dropout_share,
    with_line.long_gaps,
    with_line.long_gaps_with_dropout,
    with_line.line_trains_judged,
    with_line.line_trains_dropped,
    with_line.line_trains_never_listed,
    if(
        with_line.line_trains_judged >= {{ var('board_dropout_min_trains') }},
        1e0 * with_line.line_trains_dropped / with_line.line_trains_judged
    ) as line_dropout_share,
    with_line.line_long_gaps,
    with_line.line_long_gaps_with_dropout,
    if(
        with_line.line_long_gaps >= {{ var('board_dropout_min_long_gaps') }},
        1e0 * with_line.line_long_gaps_with_dropout / with_line.line_long_gaps
    ) as line_long_gap_dropout_share,
    with_line.is_measurement_station,
    with_line.line_measurement_long_gaps,
    with_line.line_measurement_long_gaps_with_dropout,
    {{ row_key(['with_line.week_start', 'with_line.line_id', 'with_line.naptan_id']) }}
        as board_reliability_key
from with_line
left join {{ ref('seed_board_dropout_lines') }} as dropout_lines
    on with_line.line_id = dropout_lines.line_id
left join {{ ref('dim_station') }} as dim_station
    on with_line.naptan_id = dim_station.naptan_id
