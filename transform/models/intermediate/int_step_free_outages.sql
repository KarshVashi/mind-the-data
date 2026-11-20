{{ config(materialized='table', tags=['station']) }}

-- Step-free outage episodes per station (D73): first and last slot seen,
-- hours seen, and the hours of unobserved slots inside the episode, kept
-- apart. Built from int_step_free_slots, whose header has the episode rules.
--
-- hours_seen is a lower bound on the outage: the feed lists only faults
-- that exist at the poll and has no clear time, so the outage started
-- before first_seen and ended after last_seen by an amount not known.
-- hours_missing_inside is time between two sightings when no poll
-- succeeded: neither fixed nor broken, not part of hours_seen. An episode
-- that starts at the feed's first observed slot was already broken when
-- collection began (starts_at_feed_start); one that is listed at the feed's
-- latest observed slot may still be going (open_at_feed_end).

with feed_bounds as (
    select
        min(date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)))
            as first_observed_slot_utc,
        max(date_add('minute', -(minute(polled_at) % 5), date_trunc('minute', polled_at)))
            as last_observed_slot_utc
    from {{ ref('stg_polls') }}
    where
        feed = 'stop_disruption'
        and is_ok
        and dt >= date '{{ var("raw_start_date") }}'
),

episodes as (
    select
        episode_key,
        naptan_id,
        min(slot_utc) as first_seen_slot_utc,
        max(slot_utc) as last_seen_slot_utc,
        count(*) as seen_slots,
        sum(missing_slots_before) as missing_slots,
        count_if(is_planned) as planned_slots
    from {{ ref('int_step_free_slots') }}
    group by episode_key, naptan_id
)

select
    episodes.episode_key,
    episodes.naptan_id,
    episodes.first_seen_slot_utc,
    episodes.last_seen_slot_utc,
    episodes.seen_slots / 12e0 as hours_seen,
    episodes.missing_slots / 12e0 as hours_missing_inside,
    episodes.planned_slots / 12e0 as planned_hours_seen,
    episodes.first_seen_slot_utc = feed_bounds.first_observed_slot_utc as starts_at_feed_start,
    episodes.last_seen_slot_utc = feed_bounds.last_observed_slot_utc as open_at_feed_end
from episodes
cross join feed_bounds
