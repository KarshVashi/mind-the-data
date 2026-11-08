-- int_scheduled_headways drops departures whose schedule name has no day
-- type, so a new TfL schedule name fails here instead of silently leaving
-- a station without scheduled service.
select distinct
    timetable.line_id,
    timetable.schedule_name
from {{ ref('stg_timetable') }} as timetable
left join {{ ref('timetable_schedule_day_types') }} as day_types
    on timetable.schedule_name = day_types.schedule_name
where
    timetable.dt >= date '{{ var("raw_start_date") }}'
    and day_types.schedule_name is null
