-- int_scheduled_headways pools a timetable's departures by day type and,
-- for a single-weekday schedule, by weekday (D67). Two schedules of one
-- timetable on the same day type and weekday (say "Monday - Friday" and
-- "Friday", or "Tuesdays" listed twice) would double that day's trains and
-- halve its headways. A generic schedule beside a single-weekday one is
-- fine: the model gives the single-weekday one its own weekday.
select
    timetable.poll_id,
    timetable.request_path,
    day_types.day_type,
    -- 0 stands for a generic schedule (no weekday of its own), so generic
    -- names group together; ISO weekdays run 1 to 7.
    coalesce(day_types.iso_day_of_week, 0) as iso_day_of_week,
    count(distinct timetable.schedule_name) as schedules
from {{ ref('stg_timetable') }} as timetable
inner join {{ ref('timetable_schedule_day_types') }} as day_types
    on timetable.schedule_name = day_types.schedule_name
where timetable.dt >= date '{{ var("raw_start_date") }}'
group by
    timetable.poll_id,
    timetable.request_path,
    day_types.day_type,
    coalesce(day_types.iso_day_of_week, 0)
having count(distinct timetable.schedule_name) > 1
