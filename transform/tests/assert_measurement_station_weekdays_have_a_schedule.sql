-- D67: at a measurement station, a weekday of a timetabled day type with
-- no schedule (generic or its own) would drop that day's hours from the
-- measured view without a trace. That changes the league table, so it
-- stops the build. Elsewhere it warns:
-- assert_station_weekdays_have_a_schedule.
{{ timetable_weekday_coverage_gaps(measurement_stations=true) }}
