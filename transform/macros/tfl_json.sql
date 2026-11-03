{#
  ISO 8601 text with a zone to a UTC timestamp(6). Iceberg on Athena stores
  timestamps at microsecond precision and without a zone, so the value is
  shifted to UTC first and then the zone is dropped.
#}
{% macro utc_timestamp(expr) %}
    cast(from_iso8601_timestamp({{ expr }}) at time zone 'UTC' as timestamp(6))
{%- endmacro %}


{#
  A TfL date field as a UTC timestamp(6). TfL fills unset dates with the .NET
  minimum value '0001-01-01T00:00:00' (no zone), which becomes null rather
  than a real date two thousand years ago.
#}
{% macro tfl_timestamp(expr) %}
    case
        when {{ expr }} is null or {{ expr }} like '0001-01-01%' then null
        else {{ utc_timestamp(expr) }}
    end
{%- endmacro %}


{# A JSON string field, with TfL's empty strings read as null. #}
{% macro json_text(json_expr, path) %}
    nullif(json_extract_scalar({{ json_expr }}, '{{ path }}'), '')
{%- endmacro %}


{#
  The JSON array at `path` as array(json), or a one-element array holding
  null when the array is missing or empty. Unnesting it keeps the parent row
  (with null children) instead of dropping it, which a plain cross join
  unnest would do.
#}
{% macro json_array_or_null(json_expr, path) %}
    if(
        coalesce(cardinality(cast(json_extract({{ json_expr }}, '{{ path }}') as array(json))), 0) = 0,
        array[cast(null as json)],
        cast(json_extract({{ json_expr }}, '{{ path }}') as array(json))
    )
{%- endmacro %}


{#
  Deterministic row key from several columns: the merge key of a staging
  model and the column its uniqueness test runs on. Nulls are spelled out so
  (null, 'a') and ('', 'a') do not collide.
#}
{% macro row_key(columns) %}
    to_hex(md5(to_utf8(concat_ws(
        '|',
        {%- for column in columns %}
        coalesce(cast({{ column }} as varchar), '<null>'){{ ',' if not loop.last }}
        {%- endfor %}
    ))))
{%- endmacro %}
