{{ config(materialized='table', tags=['station']) }}

-- One row per station and 5-minute UTC slot in which a successful
-- stop_disruption poll listed a step-free access outage for the station
-- (D73), with the outage episode the slot belongs to.
--
-- The feed lists only faults that exist now and gives no clear time, so an
-- outage can only be seen, never timed. A slot is "observed" when at least
-- one successful poll fell in it. Between two seen slots of a station:
--   - if an observed slot lies between them, the station was polled and the
--     outage was not listed: the first episode ended and a new one starts;
--   - otherwise every slot between them is unobserved (missed polls). That
--     is unknown, neither fixed nor still broken, so the two seen slots stay
--     in one episode and the unobserved slots are counted apart in
--     missing_slots_before. They are never added to the hours seen.
--
-- Step-free text follows is_step_free_outage_text. Closure and Part Closure
-- types are closures, and appearance Information is a standing notice: both
-- are left out, as in fct_station_disruption_weekly. A disruption counts
-- when in force at the poll (valid_from <= poll < valid_to, either open).

with polls as (
    select date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)) as slot_utc
    from {{ ref('stg_polls') }}
    where
        feed = 'stop_disruption'
        and is_ok
        and dt >= date '{{ var("raw_start_date") }}'
),

observed_slots as (
    -- Numbered in time order, so the observed slots between two seen slots
    -- are the difference of their numbers: no range join.
    select
        slot_utc,
        row_number() over (order by slot_utc) as slot_number
    from (select distinct slot_utc from polls) as distinct_slots
),

step_free_points as (
    -- stg_stop_disruption repeats a point once per concerned line.
    select distinct
        poll_id,
        request_path,
        disruption_ordinal,
        polled_at,
        is_planned,
        date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)) as slot_utc,
        coalesce(station_atco_code, atco_code) as naptan_id
    from {{ ref('stg_stop_disruption') }}
    where
        dt >= date '{{ var("raw_start_date") }}'
        and coalesce(station_atco_code, atco_code) is not null
        and coalesce(disruption_type, '') not in ('Closure', 'Part Closure')
        and coalesce(appearance, '') <> 'Information'
        and (valid_from is null or valid_from <= polled_at)
        and (valid_to is null or polled_at < valid_to)
        and {{ is_step_free_outage_text("description", "additional_information") }}
),

seen_slots as (
    -- A slot is planned only when every outage seen in it is planned work.
    select
        naptan_id,
        slot_utc,
        bool_and(is_planned) as is_planned
    from step_free_points
    group by naptan_id, slot_utc
),

with_previous as (
    -- A seen slot always has a successful poll, so it is an observed slot.
    select
        seen_slots.naptan_id,
        seen_slots.slot_utc,
        seen_slots.is_planned,
        lag(seen_slots.slot_utc) over (
            partition by seen_slots.naptan_id order by seen_slots.slot_utc
        ) as previous_slot_utc,
        observed_slots.slot_number
        - lag(observed_slots.slot_number) over (
            partition by seen_slots.naptan_id order by seen_slots.slot_utc
        ) - 1 as observed_slots_between
    from seen_slots
    inner join observed_slots
        on seen_slots.slot_utc = observed_slots.slot_utc
),

episodes as (
    select
        *,
        previous_slot_utc is null or observed_slots_between > 0 as starts_episode
    from with_previous
),

numbered as (
    select
        *,
        sum(if(starts_episode, 1, 0)) over (
            partition by naptan_id order by slot_utc
        ) as episode_number
    from episodes
),

episode_starts as (
    select
        naptan_id,
        episode_number,
        min(slot_utc) as episode_first_slot_utc
    from numbered
    group by naptan_id, episode_number
)

select
    numbered.naptan_id,
    numbered.slot_utc,
    numbered.is_planned,
    cast(numbered.episode_number as integer) as episode_number,
    {{ row_key(['numbered.naptan_id', 'episode_starts.episode_first_slot_utc']) }} as episode_key,
    -- Unobserved slots between this seen slot and the last one of the same
    -- episode; 0 for the first slot of an episode.
    if(
        numbered.starts_episode,
        0,
        date_diff('minute', numbered.previous_slot_utc, numbered.slot_utc) / 5 - 1
    ) as missing_slots_before,
    {{ row_key(['numbered.naptan_id', 'numbered.slot_utc']) }} as step_free_slot_key
from numbered
inner join episode_starts
    on
        numbered.naptan_id = episode_starts.naptan_id
        and numbered.episode_number = episode_starts.episode_number
