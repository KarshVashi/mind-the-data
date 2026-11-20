{{ config(materialized='table', tags=['station']) }}

-- Step-free outages per ISO week (London) and station (D73): how often
-- step-free access was seen to be out, from the 5-minute stop disruption
-- feed. One row per week for every station with an outage seen in any week,
-- so a quiet week reads as 0 hours beside its observed_share rather than as
-- no row. Stations that never had an outage seen have no row.
--
-- Hours are a lower bound. The feed lists faults that exist now and gives no
-- clear time, so a sighting says nothing about how long the lift had been
-- out before the first poll that listed it, or stayed out after the last.
-- Time with no successful poll inside an episode is counted in
-- missing_hours_inside and never in the hours seen. A week is the slots seen
-- in it (see int_step_free_slots); an episode that spans a week boundary
-- counts as one episode in each week it has a slot in, and as one started
-- episode in the week of its first slot.
--
-- Whether a station has step-free access at all is not in any data collected
-- (route sequences carry no accessibility fields), so there is no denominator
-- of step-free stations here.
--
-- repeat_offender is null until the week has three weeks of feed before it.
-- It is true when at least 3 of the last 4 weeks (this one included) had an
-- outage seen, false when 3 cannot be reached even if every week with fewer
-- than half its slots observed had one, and null otherwise.

with weeks as (
    select distinct
        week_start,
        iso_week,
        expected_slots,
        observed_slots,
        observed_share
    from {{ ref('fct_station_disruption_weekly') }}
),

slot_weeks as (
    select
        slots.naptan_id,
        slots.episode_key,
        slots.is_planned,
        slots.missing_slots_before,
        date_trunc('week', cast({{ london_local('slots.slot_utc') }} as date))
            as week_start
    from {{ ref('int_step_free_slots') }} as slots
),

episode_firsts as (
    select
        episode_key,
        date_trunc('week', cast({{ london_local('first_seen_slot_utc') }} as date))
            as first_week_start
    from {{ ref('int_step_free_outages') }}
),

weekly as (
    select
        slot_weeks.naptan_id,
        slot_weeks.week_start,
        count(*) as seen_slots,
        count_if(slot_weeks.is_planned) as planned_slots,
        sum(slot_weeks.missing_slots_before) as missing_slots,
        count(distinct slot_weeks.episode_key) as episodes,
        count(distinct if(
            episode_firsts.first_week_start = slot_weeks.week_start, slot_weeks.episode_key
        )) as episodes_started
    from slot_weeks
    inner join episode_firsts
        on slot_weeks.episode_key = episode_firsts.episode_key
    group by slot_weeks.naptan_id, slot_weeks.week_start
),

stations as (
    select distinct naptan_id
    from slot_weeks
),

station_weeks as (
    select
        stations.naptan_id,
        weeks.week_start,
        weeks.iso_week,
        weeks.expected_slots,
        weeks.observed_slots,
        weeks.observed_share,
        cast(coalesce(weekly.episodes, 0) as integer) as episodes,
        cast(coalesce(weekly.episodes_started, 0) as integer) as episodes_started,
        coalesce(weekly.seen_slots, 0) as seen_slots,
        coalesce(weekly.planned_slots, 0) as planned_slots,
        coalesce(weekly.missing_slots, 0) as missing_slots
    from stations
    cross join weeks
    left join weekly
        on
            stations.naptan_id = weekly.naptan_id
            and weeks.week_start = weekly.week_start
),

windowed as (
    select
        *,
        count(*) over (
            partition by naptan_id order by week_start rows between 3 preceding and current row
        ) as weeks_in_window,
        count_if(seen_slots > 0) over (
            partition by naptan_id order by week_start rows between 3 preceding and current row
        ) as outage_weeks_last4,
        count_if(seen_slots = 0 and observed_share < 0.5) over (
            partition by naptan_id order by week_start rows between 3 preceding and current row
        ) as unobserved_weeks_last4
    from station_weeks
)

select
    windowed.week_start,
    windowed.iso_week,
    windowed.naptan_id,
    dim_station.station_name,
    dim_station.line_ids,
    windowed.episodes,
    windowed.episodes_started,
    windowed.outage_weeks_last4,
    windowed.expected_slots,
    windowed.observed_slots,
    windowed.observed_share,
    windowed.seen_slots / 12e0 as outage_hours_seen,
    windowed.planned_slots / 12e0 as planned_hours_seen,
    (windowed.seen_slots - windowed.planned_slots) / 12e0 as unplanned_hours_seen,
    windowed.missing_slots / 12e0 as missing_hours_inside,
    case
        when windowed.weeks_in_window < 4 then null
        when windowed.outage_weeks_last4 >= 3 then true
        when windowed.outage_weeks_last4 + windowed.unobserved_weeks_last4 < 3 then false
    end as repeat_offender,
    {{ row_key(['windowed.naptan_id', 'windowed.week_start']) }} as step_free_weekly_key
from windowed
left join {{ ref('dim_station') }} as dim_station
    on windowed.naptan_id = dim_station.naptan_id
