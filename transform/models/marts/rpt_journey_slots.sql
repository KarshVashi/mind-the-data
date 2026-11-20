-- Tagged `exploratory`: the daily prod build excludes it (ADR-018).
{{ config(materialized='table', tags=['exploratory']) }}

-- Should I Run (ADR-018, D76): what a 15-minute window at station A is
-- like for someone going to station B. One row per line, direction,
-- origin, destination and weekday slot (`journey_slot_minutes` long,
-- inside `journey_slot_windows`), over every weekday in the data. The unit
-- is the window, not a named departure: Tube timetables have no trip ids,
-- and trains come every two minutes or so.
--
-- A day counts for a slot when the origin's station-direction-hour holding
-- the slot was observed and passed the quality gate (is_in_band, ADR-014).
-- An hour with no row, not observed or out of band is never read as a good
-- day: it is left out, and `passes_gate` says whether enough of the
-- observed days passed (at most `gate_max_out_of_band_share` out of band).
--
-- Per slot, over the counted days:
-- - runs: trains matched from A to B by vehicle id
--   (int_station_vehicle_runs) whose pass at B fell in an observed hour,
--   with run time p10, p50 and p90 (nearest rank on exact seconds);
-- - trains seen per day at A in the direction (all vehicle-id passes,
--   whether or not they were matched at B), and the timetable's departures
--   per day from A in the slot (the latest timetable fetch of the stop, by
--   the day's day type; null where TfL serves no timetable);
-- - the wait at A for a train that reaches B: gaps between consecutive
--   matched trains, each belonging to the slot of the train that ends it.
--   Each day's expected wait for someone arriving at random is
--   sum(gap^2) / (2 sum(gap)) (PLAN 7.3); typical = the median day's,
--   worst = p90 over days of the day's longest gap in the slot. A train
--   that went unmatched (an id lost at B) widens a
--   gap, so both read long, never short. A gap is dropped when the train
--   before it was in an hour that was not observed.
--
-- `status` is `ok` only when the slot passes the gate and has at least
-- `journey_min_runs` runs over at least `journey_min_weeks` ISO weeks;
-- otherwise `not_enough_data`, and the publisher withholds the figures.

{%- set slot = var('journey_slot_minutes') %}

with runs as (
    select
        line_id,
        direction,
        vehicle_id,
        origin_naptan_id,
        destination_naptan_id,
        origin_pass_time,
        run_seconds,
        london_date,
        origin_london_minute,
        origin_london_hour,
        destination_london_date,
        destination_london_hour,
        cast(floor(origin_london_minute / {{ slot }}e0) * {{ slot }} as integer)
            as slot_start_minute
    from {{ ref('int_station_vehicle_runs') }}
),

measured as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        london_hour,
        is_observed,
        is_in_band
    from {{ journey_measured_hours() }}
),

slots as (
    {%- for window in var('journey_slot_windows') %}
        select slot_starts.slot_start_minute
        from
            unnest(sequence({{ window[0] }}, {{ window[1] - slot }}, {{ slot }}))
                as slot_starts (slot_start_minute)
        {{ 'union all' if not loop.last }}
    {%- endfor %}
),

pairs as (
    select distinct
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id
    from runs
),

slot_days as (
    -- Every weekday on which the origin's hour holding the slot has a row.
    select
        pairs.line_id,
        pairs.direction,
        pairs.origin_naptan_id,
        pairs.destination_naptan_id,
        slots.slot_start_minute,
        measured.london_date,
        day_types.day_type,
        measured.is_observed,
        measured.is_observed and measured.is_in_band as is_counted
    from pairs
    cross join slots
    inner join measured
        on
            pairs.line_id = measured.line_id
            and pairs.origin_naptan_id = measured.naptan_id
            and pairs.direction = measured.direction
            and slots.slot_start_minute / 60 = measured.london_hour
    inner join {{ ref('day_types') }} as day_types
        on day_of_week(measured.london_date) = day_types.iso_day_of_week
    where day_types.day_class = 'weekday'
),

counted_runs as (
    select
        runs.*,
        row_number() over (
            partition by
                runs.line_id,
                runs.direction,
                runs.origin_naptan_id,
                runs.destination_naptan_id,
                runs.slot_start_minute
            order by runs.run_seconds, runs.origin_pass_time
        ) as run_rank,
        count(*) over (
            partition by
                runs.line_id,
                runs.direction,
                runs.origin_naptan_id,
                runs.destination_naptan_id,
                runs.slot_start_minute
        ) as run_count
    from runs
    inner join slot_days
        on
            runs.line_id = slot_days.line_id
            and runs.direction = slot_days.direction
            and runs.origin_naptan_id = slot_days.origin_naptan_id
            and runs.destination_naptan_id = slot_days.destination_naptan_id
            and runs.slot_start_minute = slot_days.slot_start_minute
            and runs.london_date = slot_days.london_date
    inner join measured as at_destination
        on
            runs.line_id = at_destination.line_id
            and runs.destination_naptan_id = at_destination.naptan_id
            and runs.direction = at_destination.direction
            and runs.destination_london_date = at_destination.london_date
            and runs.destination_london_hour = at_destination.london_hour
    where slot_days.is_counted and at_destination.is_observed
),

run_stats as (
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        count(*) as runs,
        count(distinct date_trunc('week', london_date)) as weeks_with_runs,
        {{ nearest_rank('run_seconds', 'run_rank', 'run_count', 0.1) }} as run_p10_seconds,
        {{ nearest_rank('run_seconds', 'run_rank', 'run_count', 0.5) }} as run_p50_seconds,
        {{ nearest_rank('run_seconds', 'run_rank', 'run_count', 0.9) }} as run_p90_seconds
    from counted_runs
    group by line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
),

gaps as (
    -- Consecutive matched trains at A, lookback runs included, so the first
    -- train of a window has the one before it.
    select
        runs.line_id,
        runs.direction,
        runs.origin_naptan_id,
        runs.destination_naptan_id,
        runs.london_date,
        runs.slot_start_minute,
        runs.origin_london_minute,
        date_diff(
            'second',
            lag(runs.origin_pass_time) over day_runs,
            runs.origin_pass_time
        ) as gap_seconds,
        lag(runs.london_date) over day_runs as previous_london_date,
        lag(runs.origin_london_hour) over day_runs as previous_london_hour
    from runs
    window day_runs as (
        partition by
            runs.line_id,
            runs.direction,
            runs.origin_naptan_id,
            runs.destination_naptan_id,
            runs.london_date
        order by runs.origin_pass_time, runs.vehicle_id
    )
),

counted_gaps as (
    select gaps.*
    from gaps
    inner join slot_days
        on
            gaps.line_id = slot_days.line_id
            and gaps.direction = slot_days.direction
            and gaps.origin_naptan_id = slot_days.origin_naptan_id
            and gaps.destination_naptan_id = slot_days.destination_naptan_id
            and gaps.slot_start_minute = slot_days.slot_start_minute
            and gaps.london_date = slot_days.london_date
    inner join measured as previous_hour
        on
            gaps.line_id = previous_hour.line_id
            and gaps.origin_naptan_id = previous_hour.naptan_id
            and gaps.direction = previous_hour.direction
            and gaps.previous_london_date = previous_hour.london_date
            and gaps.previous_london_hour = previous_hour.london_hour
    where
        slot_days.is_counted
        and previous_hour.is_observed
        and gaps.gap_seconds is not null
        -- A lookback run starts no slot of its own.
        and {{ in_journey_windows('gaps.origin_london_minute') }}
),

daily_waits as (
    -- One day's wait in the slot: the expected wait for someone arriving
    -- at random, and the longest gap.
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        london_date,
        count(*) as gaps,
        sum(1e0 * gap_seconds * gap_seconds) / (2 * sum(gap_seconds)) as expected_wait_seconds,
        max(gap_seconds) as longest_gap_seconds
    from counted_gaps
    where gap_seconds > 0
    group by
        line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute, london_date
),

ranked_waits as (
    select
        *,
        row_number() over (
            partition by
                line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
            order by expected_wait_seconds, london_date
        ) as expected_rank,
        row_number() over (
            partition by
                line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
            order by longest_gap_seconds, london_date
        ) as longest_rank,
        count(*) over (
            partition by
                line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
        ) as wait_days
    from daily_waits
),

wait_stats as (
    -- Typical: the median day's expected wait. Worst: the p90 day's longest
    -- gap. Both are over days, so one disrupted morning moves the worst
    -- figure, not the typical one.
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        sum(gaps) as gaps,
        {{ nearest_rank('expected_wait_seconds', 'expected_rank', 'wait_days', 0.5) }}
            as typical_wait_seconds,
        {{ nearest_rank('longest_gap_seconds', 'longest_rank', 'wait_days', 0.9) }}
            as worst_wait_p90_seconds
    from ranked_waits
    group by line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
),

origin_pass_times as (
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        {{ london_local('pass_time') }} as london_ts
    from {{ journey_passes() }}
),

origin_passes as (
    -- Every train seen at A in the direction, matched at B or not.
    select
        line_id,
        naptan_id,
        direction,
        london_date,
        cast(
            floor((hour(london_ts) * 60 + minute(london_ts)) / {{ slot }}e0) * {{ slot }}
            as integer
        ) as slot_start_minute,
        count(*) as trains_seen
    from origin_pass_times
    group by
        line_id,
        naptan_id,
        direction,
        london_date,
        cast(
            floor((hour(london_ts) * 60 + minute(london_ts)) / {{ slot }}e0) * {{ slot }}
            as integer
        )
),

timetable_fetches as (
    -- The latest fetch of each stop's timetable: the service planned now.
    select
        line_id,
        departure_stop_id,
        direction,
        max_by(poll_id, polled_at) as poll_id,
        max_by(dt, polled_at) as dt
    from {{ ref('stg_timetable') }}
    where dt >= date '{{ var("raw_start_date") }}'
    group by line_id, departure_stop_id, direction
),

timetabled as (
    select
        timetable.line_id,
        timetable.departure_stop_id as naptan_id,
        timetable.direction,
        schedule_day_types.day_type,
        timetable.departure_minute_of_service_day
    from {{ ref('stg_timetable') }} as timetable
    inner join timetable_fetches
        on
            timetable.poll_id = timetable_fetches.poll_id
            and timetable.dt = timetable_fetches.dt
            and timetable.line_id = timetable_fetches.line_id
            and timetable.departure_stop_id = timetable_fetches.departure_stop_id
            and timetable.direction = timetable_fetches.direction
    inner join {{ ref('timetable_schedule_day_types') }} as schedule_day_types
        on timetable.schedule_name = schedule_day_types.schedule_name
    where timetable.dt >= date '{{ var("raw_start_date") }}'
),

timetabled_day_types as (
    select distinct
        line_id,
        naptan_id,
        direction,
        day_type
    from timetabled
),

scheduled as (
    select
        line_id,
        naptan_id,
        direction,
        day_type,
        cast(floor(departure_minute_of_service_day / {{ slot }}e0) * {{ slot }} as integer)
            as slot_start_minute,
        count(*) as departures
    from timetabled
    group by
        line_id,
        naptan_id,
        direction,
        day_type,
        cast(floor(departure_minute_of_service_day / {{ slot }}e0) * {{ slot }} as integer)
),

day_stats as (
    select
        slot_days.line_id,
        slot_days.direction,
        slot_days.origin_naptan_id,
        slot_days.destination_naptan_id,
        slot_days.slot_start_minute,
        count_if(slot_days.is_observed) as days_observed,
        count_if(slot_days.is_counted) as days_counted,
        sum(if(slot_days.is_counted, coalesce(origin_passes.trains_seen, 0)))
            as trains_seen_on_counted_days,
        avg(
            if(
                slot_days.is_counted and timetabled_day_types.day_type is not null,
                coalesce(scheduled.departures, 0)
            )
        ) as scheduled_per_day,
        min(slot_days.london_date) as first_london_date,
        max(slot_days.london_date) as last_london_date
    from slot_days
    left join origin_passes
        on
            slot_days.line_id = origin_passes.line_id
            and slot_days.origin_naptan_id = origin_passes.naptan_id
            and slot_days.direction = origin_passes.direction
            and slot_days.london_date = origin_passes.london_date
            and slot_days.slot_start_minute = origin_passes.slot_start_minute
    left join timetabled_day_types
        on
            slot_days.line_id = timetabled_day_types.line_id
            and slot_days.origin_naptan_id = timetabled_day_types.naptan_id
            and slot_days.direction = timetabled_day_types.direction
            and slot_days.day_type = timetabled_day_types.day_type
    left join scheduled
        on
            slot_days.line_id = scheduled.line_id
            and slot_days.origin_naptan_id = scheduled.naptan_id
            and slot_days.direction = scheduled.direction
            and slot_days.day_type = scheduled.day_type
            and slot_days.slot_start_minute = scheduled.slot_start_minute
    group by
        slot_days.line_id,
        slot_days.direction,
        slot_days.origin_naptan_id,
        slot_days.destination_naptan_id,
        slot_days.slot_start_minute
),

combined as (
    select
        day_stats.line_id,
        day_stats.direction,
        day_stats.origin_naptan_id,
        day_stats.destination_naptan_id,
        day_stats.slot_start_minute,
        day_stats.days_observed,
        day_stats.days_counted,
        day_stats.first_london_date,
        day_stats.last_london_date,
        day_stats.days_observed > 0
        and day_stats.days_counted
        >= (1 - {{ var('gate_max_out_of_band_share') }}) * day_stats.days_observed
            as passes_gate,
        coalesce(run_stats.runs, 0) as runs,
        coalesce(run_stats.weeks_with_runs, 0) as weeks_with_runs,
        run_stats.run_p10_seconds,
        run_stats.run_p50_seconds,
        run_stats.run_p90_seconds,
        if(
            day_stats.days_counted > 0,
            1e0 * coalesce(run_stats.runs, 0) / day_stats.days_counted
        ) as runs_per_day,
        if(
            day_stats.days_counted > 0,
            1e0 * day_stats.trains_seen_on_counted_days / day_stats.days_counted
        ) as trains_seen_per_day,
        day_stats.scheduled_per_day,
        coalesce(wait_stats.gaps, 0) as gaps,
        wait_stats.typical_wait_seconds,
        wait_stats.worst_wait_p90_seconds
    from day_stats
    left join run_stats
        on
            day_stats.line_id = run_stats.line_id
            and day_stats.direction = run_stats.direction
            and day_stats.origin_naptan_id = run_stats.origin_naptan_id
            and day_stats.destination_naptan_id = run_stats.destination_naptan_id
            and day_stats.slot_start_minute = run_stats.slot_start_minute
    left join wait_stats
        on
            day_stats.line_id = wait_stats.line_id
            and day_stats.direction = wait_stats.direction
            and day_stats.origin_naptan_id = wait_stats.origin_naptan_id
            and day_stats.destination_naptan_id = wait_stats.destination_naptan_id
            and day_stats.slot_start_minute = wait_stats.slot_start_minute
)

select
    {{ row_key([
        'line_id', 'direction', 'origin_naptan_id', 'destination_naptan_id', 'slot_start_minute',
    ]) }} as journey_slot_key,
    line_id,
    direction,
    origin_naptan_id,
    destination_naptan_id,
    'weekday' as day_class,
    slot_start_minute,
    slot_start_minute + {{ slot }} as slot_end_minute,
    days_observed,
    days_counted,
    first_london_date,
    last_london_date,
    passes_gate,
    runs,
    weeks_with_runs,
    run_p10_seconds,
    run_p50_seconds,
    run_p90_seconds,
    runs_per_day,
    trains_seen_per_day,
    scheduled_per_day,
    gaps,
    typical_wait_seconds,
    worst_wait_p90_seconds,
    passes_gate
    and runs >= {{ var('journey_min_runs') }}
    and weeks_with_runs >= {{ var('journey_min_weeks') }} as enough_data,
    if(
        passes_gate
        and runs >= {{ var('journey_min_runs') }}
        and weeks_with_runs >= {{ var('journey_min_weeks') }},
        'ok',
        'not_enough_data'
    ) as status
from combined
