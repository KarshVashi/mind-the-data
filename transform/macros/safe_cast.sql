{#
  Overrides dbt_athena's athena__safe_cast (checked against dbt-athena
  1.11.1), which dbt unit tests use for every fixture value. A YAML list in
  a dict fixture row (an array column, e.g. dim_station.line_ids) reaches it
  as a Python list and would render as `['a']`, which is not SQL; it is
  rendered as array['a'] instead. Every other value casts as dbt_athena does.
#}
{% macro athena__safe_cast(field, type) -%}
    {%- if field is iterable and field is not string and field is not mapping -%}
        try_cast(array[
            {%- for item in field -%}
                {{ 'null' if item is none else dbt.string_literal(dbt.escape_single_quotes(item | string)) }}{{ ', ' if not loop.last }}
            {%- endfor -%}
        ] as {{ type }})
    {%- else -%}
        try_cast({{ field }} as {{ type }})
    {%- endif -%}
{%- endmacro %}
