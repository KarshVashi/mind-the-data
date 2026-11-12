-- int_scheduled_headways pools a timetable's departures by day type. Two
-- schedules of one timetable on the same day type (say "Monday - Friday"
-- and "Friday") would double that day's trains and halve its headways.
select
    timetable.poll_id,
    timetable.request_path,
    day_types.day_type,
    count(distinct timetable.schedule_name) as schedules
from {{ ref('stg_timetable') }} as timetable
inner join {{ ref('timetable_schedule_day_types') }} as day_types
    on timetable.schedule_name = day_types.schedule_name
where timetable.dt >= date '{{ var("raw_start_date") }}'
group by timetable.poll_id, timetable.request_path, day_types.day_type
having count(distinct timetable.schedule_name) > 1
