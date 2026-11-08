{#
  A UTC timestamp as London wall-clock time (timestamp(6), no zone).

  The only place a time zone is named. Everything London-specific that
  follows (day type, time band, service hours) comes from the seeds
  day_types, time_bands and line_operating_hours, read against this value.
  The zone database handles both clock changes, so the late-October Sunday
  has 25 London hours and the late-March Sunday 23.

  The UTC value is shifted by London's offset at that instant. Casting the
  zoned value to timestamp would be shorter, but Athena keeps the UTC
  instant on that cast, so 07:30 UTC in summer stays 07:30, not 08:30.
#}
{% macro london_local(utc_expr) %}
    {%- set zoned = "at_timezone(with_timezone(" ~ utc_expr ~ ", 'UTC'), 'Europe/London')" -%}
    date_add(
        'minute',
        timezone_hour({{ zoned }}) * 60 + timezone_minute({{ zoned }}),
        {{ utc_expr }}
    )
{%- endmacro %}


{#
  Predicate keeping a date column inside the current run's window
  (macros/staging_window.sql), as date literals.
#}
{% macro dt_in_window(column='dt') %}
    {%- set window = staging_window() -%}
    {{ column }} between date '{{ window.start }}' and date '{{ window.end }}'
{%- endmacro %}
