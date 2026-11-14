{#
  Weekly Iceberg maintenance (PLAN M7): OPTIMIZE then VACUUM on the curated
  tables that live from one run to the next.

  Only incremental Iceberg models qualify (D64). A model materialised as a
  table is rebuilt as a new table on every run, so it never collects small
  files or old snapshots; compacting it would only rescan it. OPTIMIZE is
  bounded to the partitions the daily merges have rewritten since the last
  maintenance (`maintenance_lookback_days`), unless `full` is set.

  transform-daily.yml runs it on Sundays (London date) after the build:
    dbt run-operation iceberg_maintenance --target prod
    dbt run-operation iceberg_maintenance --args '{dry_run: true}'
#}

{% macro iceberg_maintenance_targets(nodes) %}
{%- set targets = [] -%}
{%- for node in nodes | sort(attribute='name') -%}
        {%- if node.resource_type == 'model'
            and node.config.enabled
            and node.config.materialized == 'incremental'
            and (node.config.get('table_type') or '') | lower == 'iceberg' -%}
{%- do targets.append(node) -%}
{%- endif -%}
{%- endfor -%}
{{ return(targets) }}
{% endmacro %}

{% macro iceberg_maintenance_sql(schema, identifier, partitioned_by=none, since=none) %}
{%- set pattern = '[a-z][a-z0-9_]*' -%}
{%- for name in [schema, identifier] -%}
{%- if not modules.re.fullmatch(pattern, name) -%}
{{ exceptions.raise_compiler_error('iceberg_maintenance: not a plain identifier: ' ~ name) }}
{%- endif -%}
{%- endfor -%}
{%- set relation = schema ~ '.' ~ identifier -%}
{%- set optimize = 'optimize ' ~ relation ~ ' rewrite data using bin_pack' -%}
{%- set partition = (partitioned_by or [none])[0] -%}
{%- if since is not none and partition is not none and modules.re.fullmatch(pattern, partition) -%}
{%- if not modules.re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', since) -%}
{{ exceptions.raise_compiler_error('iceberg_maintenance: since must be YYYY-MM-DD: ' ~ since) }}
{%- endif -%}
{%- set optimize = optimize ~ ' where ' ~ partition ~ " >= date '" ~ since ~ "'" -%}
{%- endif -%}
{{ return([optimize, 'vacuum ' ~ relation]) }}
{% endmacro %}

{% macro iceberg_maintenance(dry_run=false, full=false) %}
{%- if execute -%}
        {%- set since = none if full else (
            run_started_at.date() - modules.datetime.timedelta(days=var('maintenance_lookback_days'))
        ).isoformat() -%}
{%- for node in iceberg_maintenance_targets(graph.nodes.values()) -%}
            {%- set statements = iceberg_maintenance_sql(
                node.schema, node.alias, node.config.get('partitioned_by'), since
            ) -%}
{%- if dry_run -%}
{%- for sql in statements -%}
                    {{ log('iceberg maintenance (dry run): ' ~ sql, info=true) }}
                {%- endfor -%}
            {%- elif adapter.get_relation(database=node.database, schema=node.schema, identifier=node.alias) is none -%}
                {{ log('iceberg maintenance: ' ~ node.name ~ ' is not built yet; skipped', info=true) }}
            {%- else -%}
{%- for sql in statements -%}
{{ log('iceberg maintenance: ' ~ sql, info=true) }}
{#- Repeats while Athena answers ICEBERG_*_MORE_RUNS_NEEDED. -#}
{%- do adapter.run_operation_with_potential_multiple_runs(sql, sql.split(' ')[0]) -%}
{%- endfor -%}
{%- endif -%}
{%- endfor -%}
{%- endif -%}
{% endmacro %}
