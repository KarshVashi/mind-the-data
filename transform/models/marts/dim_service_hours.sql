{{ config(materialized='table') }}

-- Scheduled operating minutes per line, London date and time band, from
-- line_operating_hours. Real minutes, so the clock-change Sundays count 25
-- and 23 London hours as they happened.

select
    line_id,
    london_date,
    week_start,
    day_type,
    day_class,
    time_band,
    max(time_weight) as time_weight,
    count(*) as scheduled_minutes,
    count(*) / 60e0 as scheduled_hours,
    {{ row_key(['line_id', 'london_date', 'time_band']) }} as service_hours_key
from {{ ref('int_line_minute_status') }}
where is_scheduled
group by line_id, london_date, week_start, day_type, day_class, time_band
