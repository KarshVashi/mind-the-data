{#
  Helpers for the Should I Run models (ADR-018, D76): int_station_vehicle_runs,
  rpt_station_reach and rpt_journey_slots.

  `journey_source` picks the passes and the gate they are read with:
  `station` (default) is the every-station feed (int_station_vehicle_passes,
  int_station_measured_hours, M8); `measurement` is the 50 measurement
  stations (int_vehicle_passes, int_measured_hours), whose history starts
  on 4 November and which is used to check the method on more weeks while
  the every-station history is short. Both pairs have the same columns.
#}
{% macro journey_passes() %}
    {%- if var('journey_source') == 'measurement' -%}
        {{ return(ref('int_vehicle_passes')) }}
    {%- elif var('journey_source') == 'station' -%}
        {{ return(ref('int_station_vehicle_passes')) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error("journey_source must be 'station' or 'measurement'") }}
    {%- endif -%}
{% endmacro %}


{% macro journey_measured_hours() %}
    {%- if var('journey_source') == 'measurement' -%}
        {{ return(ref('int_measured_hours')) }}
    {%- else -%}
        {{ return(ref('int_station_measured_hours')) }}
    {%- endif -%}
{% endmacro %}


{#
  The stops of every route in the latest route-sequence fetch of each line
  and direction, as dim_station reads them, for the lines that link by
  vehicle id. One row per route and position: a loop (the Circle) lists a
  stop twice, at two positions.
#}
{% macro latest_route_stops() %}
    with latest_fetches as (
        select
            line_id,
            direction,
            max_by(poll_id, polled_at) as poll_id,
            max_by(request_path, polled_at) as request_path
        from {{ ref('stg_route_sequence') }}
        where dt >= date '{{ var("raw_start_date") }}'
        group by line_id, direction
    )

    select
        route_stops.line_id,
        route_stops.direction,
        route_stops.route_ordinal,
        route_stops.route_name,
        route_stops.stop_sequence,
        route_stops.naptan_id
    from {{ ref('stg_route_sequence') }} as route_stops
    inner join latest_fetches
        on
            route_stops.poll_id = latest_fetches.poll_id
            and route_stops.request_path = latest_fetches.request_path
    inner join {{ ref('seed_vehicle_id_lines') }} as linked
        on
            route_stops.line_id = linked.line_id
            and linked.link_by_vehicle_id
    where route_stops.dt >= date '{{ var("raw_start_date") }}'
{% endmacro %}


{#
  True when a London minute of the day falls in one of the slot windows
  (`journey_slot_windows`, [start, end) in minutes), widened at the start
  by `lookback` minutes.
#}
{% macro in_journey_windows(minute_expr, lookback=0) %}

    (
    {%- for window in var('journey_slot_windows') %}

            ({{ minute_expr }} >= {{ window[0] - lookback }} and {{ minute_expr }} < {{ window[1] }})
        {{- ' or' if not loop.last }}
    {%- endfor %}
    )
{%- endmacro %}


{# Nearest-rank percentile `p` of `value` over rows numbered `rank` of `n`. #}
{% macro nearest_rank(value, rank, n, p) %}
    min(if({{ rank }} >= ceil({{ p }} * {{ n }}), {{ value }}))
{%- endmacro %}
