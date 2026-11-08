-- Disruption Index league for the latest complete London week: one row per
-- in-scope line. Lines without a row in fct_line_weekly for that week still
-- appear, with null metrics, so a gap is visible rather than dropped.
-- Compile with `dbt compile --select league_preview` and run the SQL in
-- target/compiled with scripts/athena_query.py. Pass
-- --vars '{preview_week_start: "2026-11-02"}' to look at another week.

with latest_week as (
    {% if var('preview_week_start') %}
        select date '{{ var("preview_week_start") }}' as week_start
    {% else %}
        select max(week_start) as week_start
        from {{ ref('fct_line_weekly') }}
        where is_week_complete
    {% endif %}
)

select
    in_scope_lines.line_name,
    in_scope_lines.mode_name,
    latest_week.week_start,
    weekly.disruption_rank,
    weekly.rank_movement,
    weekly.incidents,
    weekly.median_recovery_minutes,
    weekly.is_provisional,
    in_scope_lines.is_low_volume,
    round(weekly.disruption_index, 1) as disruption_index,
    round(weekly.good_service_pct, 1) as good_service_pct,
    round(weekly.incidents_per_100_hours, 2) as incidents_per_100_hours,
    round(weekly.completeness, 3) as completeness
from {{ ref('dim_lines') }} as in_scope_lines
cross join latest_week
left join {{ ref('fct_line_weekly') }} as weekly
    on
        in_scope_lines.line_id = weekly.line_id
        and latest_week.week_start = weekly.week_start
order by weekly.disruption_rank asc nulls last, in_scope_lines.line_name asc
