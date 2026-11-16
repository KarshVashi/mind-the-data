{#
  Drops `__dbt_tmp` tables left in the target schema by a run that failed
  part way. dbt-athena creates them for incremental merges and unit tests and
  drops them on success; after a failure the next run stops with
  TABLE_ALREADY_EXISTS on the same model until someone removes them by hand.

  transform-daily.yml runs it before the build:
    dbt run-operation drop_stale_tmp_relations --target prod
    dbt run-operation drop_stale_tmp_relations --args '{dry_run: true}'

  Runs share the `transform-prod` concurrency group, so nothing else is
  building when this runs and every `__dbt_tmp` table is a leftover.
#}

{% macro stale_tmp_identifiers(identifiers) %}
{%- set stale = [] -%}
{%- for name in identifiers | sort -%}
{%- if modules.re.fullmatch('[a-z][a-z0-9_]*__dbt_tmp', name | lower) -%}
{%- do stale.append(name) -%}
{%- endif -%}
{%- endfor -%}
{{ return(stale) }}
{% endmacro %}

{% macro drop_stale_tmp_relations(dry_run=false) %}
{%- if execute -%}
{%- set schema_relation = api.Relation.create(database=target.database, schema=target.schema) -%}
{%- set relations = adapter.list_relations_without_caching(schema_relation) -%}
{%- set by_name = {} -%}
{%- for relation in relations -%}
{%- do by_name.update({relation.identifier: relation}) -%}
{%- endfor -%}
{%- set stale = stale_tmp_identifiers(by_name.keys() | list) -%}
{%- if not stale -%}
{{ log('stale tmp tables: none in ' ~ target.schema, info=true) }}
{%- endif -%}
{%- for name in stale -%}
{%- if dry_run -%}
{{ log('stale tmp tables (dry run): would drop ' ~ target.schema ~ '.' ~ name, info=true) }}
{%- else -%}
{{ log('stale tmp tables: dropping ' ~ target.schema ~ '.' ~ name, info=true) }}
{%- do adapter.drop_relation(by_name[name]) -%}
{%- endif -%}
{%- endfor -%}
{%- endif -%}
{% endmacro %}
