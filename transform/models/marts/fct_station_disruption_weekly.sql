{{ config(materialized='table') }}

-- Station disruption per ISO week (London) and station (M8), from the
-- network-wide stop disruption feed polled every 5 minutes.
--
-- Time is counted in 5-minute UTC slots, the feed's cadence. A slot is
-- observed when at least one stop_disruption poll in it succeeded. A
-- station is disrupted in a category for a slot when an observed poll in
-- that slot lists a disruption of that category for it, in force at the
-- poll (valid_from <= poll < valid_to, either bound may be open). Hours are
-- disrupted slots / 12, so each counts once however many polls or
-- disruptions it holds. London offsets are whole hours, so slots never
-- straddle a week.
--
-- Only observed slots can count: a failed or missing poll is never read as
-- "no disruption". It lowers observed_share instead, so hours are a lower
-- bound when observed_share < 1. The week's slots run from its London
-- Monday 00:00 to its end or the feed's latest poll attempt, whichever is
-- first; a week before the feed started counts its slots before the first
-- poll as missed.
--
-- Categories, in this order: closure (TfL type Closure or Part Closure: the
-- station closed or not served by some of its trains), step_free (the text
-- names step-free access or a lift), other. Standing notices (appearance
-- Information, such as "travel in the front 8 coaches") are not
-- disruptions and are left out. TfL gives disruptions no id, so a
-- disruption is (station, type, appearance, description).
--
-- One row per week and station in dim_station, plus any other station with
-- a counted disruption that week, so an undisrupted station reads as 0
-- hours beside its observed_share rather than as no row.

with polls as (
    select
        polled_at,
        is_ok,
        date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)) as slot_utc
    from {{ ref('stg_polls') }}
    where
        feed = 'stop_disruption'
        and dt >= date '{{ var("raw_start_date") }}'
),

feed_bounds as (
    select
        min(slot_utc) as first_slot_utc,
        max(slot_utc) as last_slot_utc
    from polls
),

calendar_days as (
    -- From a week before the first poll, so the first week starts at its
    -- London Monday; built per day because sequence() stops at 10,000.
    select calendar_days.calendar_day
    from feed_bounds
    cross join
        unnest(
            sequence(
                date_add('day', -7, cast(feed_bounds.first_slot_utc as date)),
                cast(feed_bounds.last_slot_utc as date),
                interval '1' day
            )
        ) as calendar_days (calendar_day)
),

slot_offsets as (
    select slot_offsets.slot_offset
    from unnest(sequence(0, 287)) as slot_offsets (slot_offset)
),

slots as (
    select
        date_add(
            'minute',
            slot_offsets.slot_offset * 5,
            cast(calendar_days.calendar_day as timestamp(6))
        ) as slot_utc
    from calendar_days
    cross join slot_offsets
),

weekly_slots as (
    select
        slot_utc,
        date_trunc('week', cast({{ london_local('slot_utc') }} as date)) as week_start
    from slots
),

spine as (
    select weekly_slots.*
    from weekly_slots
    cross join feed_bounds
    where
        weekly_slots.slot_utc <= feed_bounds.last_slot_utc
        and weekly_slots.week_start
        >= date_trunc('week', cast({{ london_local('feed_bounds.first_slot_utc') }} as date))
),

observed_slots as (
    select distinct slot_utc
    from polls
    where is_ok
),

weeks as (
    select
        spine.week_start,
        count(*) as expected_slots,
        count(observed_slots.slot_utc) as observed_slots
    from spine
    left join observed_slots
        on spine.slot_utc = observed_slots.slot_utc
    group by spine.week_start
),

disrupted_points as (
    -- One row per disrupted point per poll: stg_stop_disruption repeats a
    -- point once per concerned line.
    select distinct
        poll_id,
        request_path,
        disruption_ordinal,
        polled_at,
        disruption_type,
        appearance,
        description,
        additional_information,
        valid_from,
        valid_to,
        coalesce(station_atco_code, atco_code) as naptan_id
    from {{ ref('stg_stop_disruption') }}
    where dt >= date '{{ var("raw_start_date") }}'
),

active as (
    select
        naptan_id,
        date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)) as slot_utc,
        {{ row_key(['naptan_id', 'disruption_type', 'appearance', 'description']) }}
            as disruption_key,
        case
            when disruption_type in ('Closure', 'Part Closure') then 'closure'
            when
                regexp_like(
                    lower(coalesce(description, '') || ' ' || coalesce(additional_information, '')),
                    'step[- ]free|\blifts?\b'
                )
                then 'step_free'
            else 'other'
        end as category
    from disrupted_points
    where
        naptan_id is not null
        and coalesce(appearance, '') <> 'Information'
        and (valid_from is null or valid_from <= polled_at)
        and (valid_to is null or polled_at < valid_to)
),

active_weekly as (
    select
        active.naptan_id,
        weekly_slots.week_start,
        count(distinct if(active.category = 'closure', active.slot_utc)) as closure_slots,
        count(distinct if(active.category = 'step_free', active.slot_utc)) as step_free_slots,
        count(distinct if(active.category = 'other', active.slot_utc)) as other_slots,
        count(distinct active.disruption_key) as disruptions
    from active
    inner join weekly_slots
        on active.slot_utc = weekly_slots.slot_utc
    group by active.naptan_id, weekly_slots.week_start
),

station_weeks as (
    select
        stations.naptan_id,
        weeks.week_start
    from {{ ref('dim_station') }} as stations
    cross join weeks
    union
    select
        naptan_id,
        week_start
    from active_weekly
)

select
    station_weeks.week_start,
    station_weeks.naptan_id,
    weeks.expected_slots,
    weeks.observed_slots,
    cast(year_of_week(station_weeks.week_start) as varchar)
    || '-W'
    || lpad(cast(week_of_year(station_weeks.week_start) as varchar), 2, '0') as iso_week,
    coalesce(active_weekly.closure_slots, 0) / 12e0 as closure_hours,
    coalesce(active_weekly.step_free_slots, 0) / 12e0 as step_free_hours,
    coalesce(active_weekly.other_slots, 0) / 12e0 as other_disruption_hours,
    cast(coalesce(active_weekly.disruptions, 0) as integer) as disruptions,
    1e0 * weeks.observed_slots / weeks.expected_slots as observed_share,
    {{ row_key(['station_weeks.naptan_id', 'station_weeks.week_start']) }}
        as station_disruption_weekly_key
from station_weeks
inner join weeks
    on station_weeks.week_start = weeks.week_start
left join active_weekly
    on
        station_weeks.naptan_id = active_weekly.naptan_id
        and station_weeks.week_start = active_weekly.week_start
