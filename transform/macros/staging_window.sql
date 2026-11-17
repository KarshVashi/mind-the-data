{#
  The raw days a staging run reads, as inclusive 'YYYY-MM-DD' strings.

  Rendered as literals, not SQL date arithmetic, so Athena's partition
  projection prunes `dt` before listing S3.

  initial_days: when given, a first build or full refresh without
  start_date reads only that many days back from today (never before
  raw_start_date) instead of all history. The station models use it
  (`station_initial_days`): the line feed is too large to read whole in one
  query, so older days are backfilled in chunks with start_date/end_date.
#}
{% macro staging_window(initial_days=none) %}
    {%- set today = modules.datetime.datetime.now(modules.pytz.utc).date() -%}
    {#- A full refresh rebuilds the table from the window alone, so a backfill
        window would silently drop every other day. -#}
    {%- if var('start_date') and flags.FULL_REFRESH -%}
        {{ exceptions.raise_compiler_error("start_date/end_date cannot be combined with --full-refresh: the table would keep only that window") }}
    {%- endif -%}
    {%- if var('start_date') -%}
        {%- set start = var('start_date') | string -%}
    {%- elif is_incremental() -%}
        {%- set start = (today - modules.datetime.timedelta(days=var('lookback_days') | int)).isoformat() -%}
    {%- elif initial_days is not none -%}
        {%- set recent = (today - modules.datetime.timedelta(days=initial_days | int)).isoformat() -%}
        {%- set start = [recent, var('raw_start_date') | string] | max -%}
    {%- else -%}
        {%- set start = var('raw_start_date') | string -%}
    {%- endif -%}
    {%- set end = (var('end_date') or today.isoformat()) | string -%}
    {%- for value in [start, end] -%}
        {%- if not modules.re.match('^\d{4}-\d{2}-\d{2}$', value) -%}
            {{ exceptions.raise_compiler_error("staging window dates must be YYYY-MM-DD, got '" ~ value ~ "'") }}
        {%- endif -%}
    {%- endfor -%}
    {%- if start > end -%}
        {{ exceptions.raise_compiler_error("staging window starts after it ends: " ~ start ~ " > " ~ end) }}
    {%- endif -%}
    {{ return({'start': start, 'end': end}) }}
{% endmacro %}


{#
  Limits the merge's scan of the existing staging table to the days a run
  can touch. Config is resolved at parse time, before is_incremental() is
  known, so the default form is relative to the query's current_date and one
  day wider than lookback_days: a run that crosses midnight UTC between
  rendering the source filter and running the merge must still match the
  oldest day it re-read, or those rows would be inserted twice.
#}
{% macro staging_incremental_predicates() %}
    {%- if var('start_date') -%}
        {{ return(["target.dt >= date '" ~ var('start_date') ~ "'"]) }}
    {%- else -%}
        {{ return(["target.dt >= current_date - interval '" ~ ((var('lookback_days') | int) + 1) ~ "' day"]) }}
    {%- endif -%}
{% endmacro %}
