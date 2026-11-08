{{ config(materialized='table') }}

-- Every UTC minute from raw_start_date up to a few minutes before the run,
-- so a minute is only listed once its poll has had time to land. Built from
-- days x minutes-of-day because Athena's sequence() stops at 10,000 entries.
with calendar_days as (
    select calendar_days.calendar_day
    from
        unnest(sequence(date '{{ var("raw_start_date") }}', current_date, interval '1' day))
            as calendar_days (calendar_day)
),

day_offsets as (
    select day_offsets.minute_offset
    from unnest(sequence(0, 1439)) as day_offsets (minute_offset)
),

spine as (
    select
        cast(calendar_days.calendar_day as date) as dt,
        date_add(
            'minute', day_offsets.minute_offset, cast(calendar_days.calendar_day as timestamp(6))
        ) as minute_utc
    from calendar_days
    cross join day_offsets
)

select
    minute_utc,
    dt
from spine
where
    minute_utc
    <= date_add(
        'minute',
        -{{ var('minute_settle_minutes') }},
        cast(date_trunc('minute', current_timestamp at time zone 'UTC') as timestamp(6))
    )
