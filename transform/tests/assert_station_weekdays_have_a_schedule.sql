{{ config(severity='warn') }}

-- D67: at a station outside seed_measurement_stations, a weekday of a
-- timetabled day type with no schedule (generic or its own) leaves the
-- station view on hours with trains only that day (no timetable spine).
-- The station views carry no league figure, so this warns; at measurement
-- stations the same check errors:
-- assert_measurement_station_weekdays_have_a_schedule.
{{ timetable_weekday_coverage_gaps(measurement_stations=false) }}
