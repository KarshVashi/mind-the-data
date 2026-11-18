-- int_scheduled_headways drops departures whose schedule name has no day
-- type, so a new TfL schedule name fails here instead of silently leaving
-- a station without scheduled service. Names listed in
-- timetable_schedules_unmodelled are known and left out on purpose (D67),
-- except at a measurement station, where leaving them out would drop
-- published scheduled hours: there they still fail.
select distinct
    timetable.line_id,
    timetable.schedule_name
from {{ ref('stg_timetable') }} as timetable
left join {{ ref('timetable_schedule_day_types') }} as day_types
    on timetable.schedule_name = day_types.schedule_name
left join {{ ref('timetable_schedules_unmodelled') }} as unmodelled
    on timetable.schedule_name = unmodelled.schedule_name
left join {{ ref('seed_measurement_stations') }} as measurement
    on
        timetable.line_id = measurement.line_id
        and timetable.departure_stop_id = measurement.naptan_id
where
    timetable.dt >= date '{{ var("raw_start_date") }}'
    and day_types.schedule_name is null
    and (unmodelled.schedule_name is null or measurement.naptan_id is not null)
