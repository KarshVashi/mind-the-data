-- rpt_weekly_status_mix must hold every scored minute of fct_line_weekly,
-- so that re-weighting it (sensitivity, PLAN 7.5) starts from the published
-- index. Returns the line-weeks whose minutes or weighted sums differ.

with mix as (
    select
        line_id,
        week_start,
        sum(scored_minutes) as scored_minutes,
        sum(weighted_disruption_minutes) as weighted_disruption_minutes,
        sum(time_weighted_scored_minutes) as time_weighted_scored_minutes
    from {{ ref('rpt_weekly_status_mix') }}
    group by line_id, week_start
)

select
    weekly.line_id,
    weekly.week_start,
    weekly.scored_minutes,
    mix.scored_minutes as mix_scored_minutes
from {{ ref('fct_line_weekly') }} as weekly
left join mix
    on weekly.line_id = mix.line_id and weekly.week_start = mix.week_start
where
    coalesce(mix.scored_minutes, 0) != weekly.scored_minutes
    or abs(coalesce(mix.weighted_disruption_minutes, 0) - weekly.weighted_disruption_minutes) > 1e-6
    or abs(coalesce(mix.time_weighted_scored_minutes, 0) - weekly.time_weighted_scored_minutes)
    > 1e-6
