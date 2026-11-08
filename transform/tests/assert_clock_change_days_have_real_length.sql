-- The London conversion (macros/london_time.sql) gives the clock-change
-- Sundays their real length, and the days around them 24 hours: 25 hours
-- on 2026-10-25 (BST to GMT) and 23 on 2027-03-28 (GMT to BST). Every
-- per-day count in the marts (scheduled minutes, completeness) rests on
-- this. Each range is under Athena's 10,000-entry sequence limit.
with utc_minutes as (
    select autumn.minute_utc
    from
        unnest(sequence(
            timestamp '2026-10-24 00:00:00', timestamp '2026-10-26 23:59:00', interval '1' minute
        )) as autumn (minute_utc)
    union all
    select spring.minute_utc
    from
        unnest(sequence(
            timestamp '2027-03-27 00:00:00', timestamp '2027-03-29 23:59:00', interval '1' minute
        )) as spring (minute_utc)
),

london_minutes as (
    select cast({{ london_local('utc_minutes.minute_utc') }} as date) as london_date
    from utc_minutes
),

london_days as (
    select
        london_date,
        count(*) as london_minutes
    from london_minutes
    group by london_date
),

expected as (
    select
        expected_days.london_date,
        expected_days.london_minutes
    from (
        values
        (date '2026-10-25', 1500),
        (date '2026-10-26', 1440),
        (date '2027-03-28', 1380),
        (date '2027-03-29', 1440)
    ) as expected_days (london_date, london_minutes)
)

select
    expected.london_date,
    expected.london_minutes as expected_minutes,
    london_days.london_minutes as actual_minutes
from expected
left join london_days
    on expected.london_date = london_days.london_date
where london_days.london_minutes is null or london_days.london_minutes <> expected.london_minutes
