{#
  `numerator` per 100 service hours, given service time in minutes; null
  when there are no minutes. Doubles throughout: Athena gives `n / 60.0` a
  decimal scale of 1, which would round 4 minutes to 0.1 hours.
#}
{% macro per_100_hours(numerator, minutes) %}
    if({{ minutes }} > 0, 6000e0 * {{ numerator }} / {{ minutes }}, null)
{%- endmacro %}


{# `part` as a percentage of `whole`; null when `whole` is 0. #}
{% macro percent_of(part, whole) %}
    if({{ whole }} > 0, 100e0 * {{ part }} / {{ whole }}, null)
{%- endmacro %}


{#
  PLAN 7.1 Disruption Index with the time-weighted denominator (D19, decided
  2026-11-14): 100 x the time-weighted mean severity weight of the scored
  minutes, so 0 is Good Service throughout and 800 is Suspended throughout.
  Null when there are no scored minutes.
#}
{% macro disruption_index(weighted_disruption_minutes, time_weighted_minutes) %}
    if({{ time_weighted_minutes }} > 0, 100e0 * {{ weighted_disruption_minutes }} / {{ time_weighted_minutes }}, null)
{%- endmacro %}
