{#
  Weekdays a timetable leaves without any schedule (D67). For each
  timetabled line, station, direction and timetable version, every ISO
  weekday of each day type the version has any schedule for must resolve to
  a generic row (null weekday) or a row of its own in
  int_scheduled_headways. A version with "Tuesdays", "Wednesdays" and
  "Thursdays" but no "Mondays" and no generic mon_thu schedule leaves
  Monday with no scheduled service, and the measured view then has no
  Monday hours there at all: the day vanishes instead of reading as
  missing. Day types the version has no schedule for are not checked; that
  is the frequency seed's and the timetable fetch's business.

  `measurement_stations` picks the rows at stations in
  seed_measurement_stations (true) or at every other station (false), so
  the two singular tests can carry different severities.
#}
{% macro timetable_weekday_coverage_gaps(measurement_stations) %}
with timetabled as (
    select distinct
        line_id,
        naptan_id,
        direction,
        valid_from,
        day_type,
        iso_day_of_week
    from {{ ref('int_scheduled_headways') }}
    where schedule_source = 'timetable'
),

day_type_versions as (
    select distinct
        line_id,
        naptan_id,
        direction,
        valid_from,
        day_type
    from timetabled
),

expected as (
    select
        versions.line_id,
        versions.naptan_id,
        versions.direction,
        versions.valid_from,
        versions.day_type,
        weekdays.iso_day_of_week
    from day_type_versions as versions
    inner join {{ ref('day_types') }} as weekdays
        on versions.day_type = weekdays.day_type
),

uncovered as (
    select
        expected.line_id,
        expected.naptan_id,
        expected.direction,
        expected.valid_from,
        expected.day_type,
        expected.iso_day_of_week
    from expected
    left join timetabled
        on
            expected.line_id = timetabled.line_id
            and expected.naptan_id = timetabled.naptan_id
            and expected.direction = timetabled.direction
            and expected.day_type = timetabled.day_type
            -- The first version has no valid_from.
            and coalesce(expected.valid_from, date '1900-01-01')
            = coalesce(timetabled.valid_from, date '1900-01-01')
            and (
                timetabled.iso_day_of_week is null
                or timetabled.iso_day_of_week = expected.iso_day_of_week
            )
    where timetabled.line_id is null
)

select
    uncovered.line_id,
    uncovered.naptan_id,
    uncovered.direction,
    uncovered.valid_from,
    uncovered.day_type,
    uncovered.iso_day_of_week
from uncovered
left join {{ ref('seed_measurement_stations') }} as stations
    on
        uncovered.line_id = stations.line_id
        and uncovered.naptan_id = stations.naptan_id
where stations.line_id is {{ 'not null' if measurement_stations else 'null' }}
{% endmacro %}
