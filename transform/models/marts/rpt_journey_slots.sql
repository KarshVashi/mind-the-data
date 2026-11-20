-- Tagged `exploratory`: the daily prod build excludes it (ADR-018).
{{ config(materialized='table', tags=['exploratory']) }}

-- Should I Run (ADR-018, D76): what a 15-minute weekday window at A is
-- like for someone going to B, per line, direction, origin, destination and
-- slot. The unit is the window, not a named departure (no Tube trip ids).
-- Method: docs/methodology.md, Should I Run.
--
-- A day counts when A's hour holding the slot was observed and either in
-- band (ADR-014) or a suspension (scheduled, no train seen or inferred: bad
-- service, not missing data), and B's hour was observed. Anything else is
-- left out, never read as a good day.
--
-- Runs: matched A-to-B trains whose B pass fell in an observed hour;
-- percentiles are nearest rank. Waits: from the time to the next matched
-- train at each instant of the slot; with none by
-- `journey_wait_lookahead_minutes` after the slot, censored there (a lower
-- bound). Unmatched trains lengthen waits, never shorten them.

{%- set slot = var('journey_slot_minutes') %}
{%- set lookahead = var('journey_wait_lookahead_minutes') %}
{%- set enough -%}
    passes_gate
    and runs >= {{ var('journey_min_runs') }}
    and weeks_with_runs >= {{ var('journey_min_weeks') }}
{%- endset %}
{%- set withheld -%}
    {%- for line in var('journey_withheld_lines') -%}
        '{{ line }}'{{ ', ' if not loop.last }}
    {%- endfor -%}
    {%- if not var('journey_withheld_lines') %}''{% endif -%}
{%- endset %}

with runs as (
    select
        line_id,
        direction,
        vehicle_id,
        origin_naptan_id,
        destination_naptan_id,
        origin_pass_time,
        origin_london_ts,
        run_seconds,
        london_date,
        origin_london_minute,
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
        is_in_band,
        is_observed
        and coalesce(scheduled_departures, 0) > 0
        and observed_trains = 0
        and inferred_arrivals = 0 as is_suspended
    from {{ journey_measured_hours() }}
    where {{ journey_history_filter('london_date') }}
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
        at_origin.london_date,
        day_types.day_type,
        at_origin.is_observed,
        at_origin.is_observed
        and (at_origin.is_in_band or at_origin.is_suspended) as is_gate_ok,
        at_origin.is_observed
        and (at_origin.is_in_band or at_origin.is_suspended)
        and coalesce(at_destination.is_observed, false) as is_counted,
        cast(at_origin.london_date as timestamp)
        + slots.slot_start_minute * interval '1' minute as slot_start_ts
    from pairs
    cross join slots
    inner join measured as at_origin
        on
            pairs.line_id = at_origin.line_id
            and pairs.origin_naptan_id = at_origin.naptan_id
            and pairs.direction = at_origin.direction
            and slots.slot_start_minute / 60 = at_origin.london_hour
    left join measured as at_destination
        on
            pairs.line_id = at_destination.line_id
            and pairs.destination_naptan_id = at_destination.naptan_id
            and pairs.direction = at_destination.direction
            and at_origin.london_date = at_destination.london_date
            and at_origin.london_hour = at_destination.london_hour
    inner join {{ ref('day_types') }} as day_types
        on day_of_week(at_origin.london_date) = day_types.iso_day_of_week
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

wait_points as (
    -- For each counted slot-day: every matched train at A after the slot
    -- starts, up to the censoring point, then the censoring point itself.
    select
        slot_days.line_id,
        slot_days.direction,
        slot_days.origin_naptan_id,
        slot_days.destination_naptan_id,
        slot_days.slot_start_minute,
        slot_days.london_date,
        slot_days.slot_start_ts,
        runs.origin_london_ts as train_ts,
        false as is_censor
    from slot_days
    inner join runs
        on
            slot_days.line_id = runs.line_id
            and slot_days.direction = runs.direction
            and slot_days.origin_naptan_id = runs.origin_naptan_id
            and slot_days.destination_naptan_id = runs.destination_naptan_id
            and slot_days.london_date = runs.london_date
            and slot_days.slot_start_ts < runs.origin_london_ts
            and slot_days.slot_start_ts + interval '{{ slot + lookahead }}' minute
            > runs.origin_london_ts
    where slot_days.is_counted
    union all
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        london_date,
        slot_start_ts,
        slot_start_ts + interval '{{ slot + lookahead }}' minute as train_ts,
        true as is_censor
    from slot_days
    where is_counted
),

wait_intervals as (
    -- Each stretch [from, until) of the slot whose next train is train_ts.
    select
        *,
        greatest(
            coalesce(lag(train_ts) over slot_day_points, slot_start_ts), slot_start_ts
        ) as from_ts,
        least(train_ts, slot_start_ts + interval '{{ slot }}' minute) as until_ts
    from wait_points
    window slot_day_points as (
        partition by
            line_id,
            direction,
            origin_naptan_id,
            destination_naptan_id,
            slot_start_minute,
            london_date
        order by train_ts, is_censor
    )
),

wait_seconds as (
    select
        *,
        date_diff('millisecond', from_ts, train_ts) / 1e3 as wait_from_seconds,
        date_diff('millisecond', until_ts, train_ts) / 1e3 as wait_until_seconds
    from wait_intervals
    where from_ts < until_ts
),

daily_waits as (
    -- The mean, over the slot, of the time to the next train (the integral
    -- of a falling wait over each stretch), and the longest wait.
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        london_date,
        sum(
            (wait_from_seconds * wait_from_seconds - wait_until_seconds * wait_until_seconds) / 2
        ) / ({{ slot }} * 60e0) as expected_wait_seconds,
        max(wait_from_seconds) as longest_wait_seconds,
        bool_or(is_censor) as is_censored
    from wait_seconds
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
            order by longest_wait_seconds, london_date
        ) as longest_rank,
        count(*) over (
            partition by
                line_id, direction, origin_naptan_id, destination_naptan_id, slot_start_minute
        ) as wait_days
    from daily_waits
),

wait_stats as (
    -- Typical: the median day's expected wait. Worst: the p90 day's longest
    -- wait. Both are over days, so one disrupted morning moves the worst
    -- figure, not the typical one.
    select
        line_id,
        direction,
        origin_naptan_id,
        destination_naptan_id,
        slot_start_minute,
        count_if(is_censored) as wait_censored_days,
        {{ nearest_rank('expected_wait_seconds', 'expected_rank', 'wait_days', 0.5) }}
            as typical_wait_seconds,
        {{ nearest_rank('longest_wait_seconds', 'longest_rank', 'wait_days', 0.9) }}
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
    where {{ journey_history_filter('london_date') }}
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
        count_if(slot_days.is_gate_ok) as days_gate_ok,
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
        and day_stats.days_gate_ok
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
        coalesce(wait_stats.wait_censored_days, 0) as wait_censored_days,
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
    wait_censored_days,
    typical_wait_seconds,
    worst_wait_p90_seconds,
    {{ enough }} as enough_data,
    case
        when line_id in ({{ withheld }}) then 'withheld'
        when {{ enough }} then 'ok'
        else 'not_enough_data'
    end as status
from combined
