{#
  Windowed rebuilds for the every-station inference models (ADR-016). Their
  input, the line feed, is too large to rebuild in full each day (the
  workgroup stops any query over 10 GiB), and a plain merge cannot remove an
  arrival a later poll disproves. So each run deletes the rows of the
  staging window's days and recomputes them, reading one extra day before
  the window so a train last seen just before midnight is still found.
  Rerunning a day gives the same rows.

  A first build or full refresh without start_date covers only the last
  `station_initial_days` days; older days are backfilled in chunks with
  start_date/end_date (docs/runbook.md, Station models).

  window_rebuild_bounds(): {'read_start', 'start', 'end'} as 'YYYY-MM-DD'
  literals, from staging_window().
#}
{% macro window_rebuild_bounds() %}
    {%- set window = staging_window(var('station_initial_days')) -%}
    {%- set read_start = (
        modules.datetime.date.fromisoformat(window.start) - modules.datetime.timedelta(days=1)
    ).isoformat() -%}
    {{ return({'read_start': read_start, 'start': window.start, 'end': window.end}) }}
{% endmacro %}


{# Pre-hook: on an incremental run, delete the window's rows of `column`. #}
{% macro delete_window_rows(column) %}
    {%- if is_incremental() -%}
        {%- set bounds = window_rebuild_bounds() -%}
        delete from {{ this }}
        where {{ column }} between date '{{ bounds.start }}' and date '{{ bounds.end }}'
    {%- endif -%}
{% endmacro %}


{#
  The merge's predicate on the existing table, as staging_incremental_predicates
  but on any date column.
#}
{% macro window_incremental_predicates(column) %}
    {%- if var('start_date') -%}
        {{ return(["target." ~ column ~ " >= date '" ~ var('start_date') ~ "'"]) }}
    {%- else -%}
        {{ return(["target." ~ column ~ " >= current_date - interval '" ~ ((var('lookback_days') | int) + 1) ~ "' day"]) }}
    {%- endif -%}
{% endmacro %}
