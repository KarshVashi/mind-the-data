{#
  Overrides dbt-athena's resolve_table_type so a unit test's temporary table
  is Iceberg, like every model in this project.

  dbt gives a unit test's temporary table a fresh config, so dbt-athena falls
  back to Hive. Hive tables only hold millisecond timestamps, and the staging
  models emit timestamp(6) for Iceberg, so every unit test failed in Athena.
  The tested model's config is not reachable here (`graph` is empty while a
  unit test runs), hence the fixed 'iceberg': a future non-Iceberg model
  would need its own handling. Everything else defers to dbt-athena.
#}
{% macro resolve_table_type() %}
    {%- if model is mapping and model.get('resource_type') == 'unit_test' -%}
        {{ return('iceberg') }}
    {%- endif -%}
    {{ return(dbt.resolve_table_type()) }}
{% endmacro %}
