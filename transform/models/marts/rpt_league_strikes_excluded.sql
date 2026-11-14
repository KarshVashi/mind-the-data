{{ config(materialized='table') }}

-- The "strikes excluded" variant of the weekly league (PLAN 7.4; DECISIONS.md
-- D60), one row per line and ISO week. The main league keeps strikes in;
-- this variant drops, for each line, the whole London dates that
-- seed_industrial_action marks as `held` or `announced` for it, then sums
-- and ranks the rest of the week with the weekly table's rules (D19, D20,
-- D22). A week with no such date for any line is the main league exactly,
-- as assert_strikes_excluded_matches_league_without_strikes checks.

with strike_days as (
    select distinct
        in_scope_lines.line_id,
        struck.london_date
    from {{ ref('seed_industrial_action') }} as actions
    inner join {{ ref('dim_lines') }} as in_scope_lines
        on
            (actions.applies_to = 'mode' and actions.applies_to_id = in_scope_lines.mode_name)
            or (actions.applies_to = 'line' and actions.applies_to_id = in_scope_lines.line_id)
    cross join
        unnest(
            sequence(actions.start_london_date, actions.end_london_date, interval '1' day)
        ) as struck (london_date)
    where actions.status in ('held', 'announced')
),

daily as (
    select
        daily.*,
        strike_days.london_date is not null as is_strike_day
    from {{ ref('fct_line_daily') }} as daily
    inner join {{ ref('dim_lines') }} as in_scope_lines
        on daily.line_id = in_scope_lines.line_id
    left join strike_days
        on
            daily.line_id = strike_days.line_id
            and daily.london_date = strike_days.london_date
),

weekly as (
    select
        line_id,
        week_start,
        count_if(is_strike_day) as strike_days,
        coalesce(sum(if(not is_strike_day, scheduled_minutes)), 0) as scheduled_minutes,
        coalesce(sum(if(not is_strike_day, observed_minutes)), 0) as observed_minutes,
        coalesce(sum(if(not is_strike_day, engineering_works_minutes)), 0)
            as engineering_works_minutes,
        coalesce(sum(if(not is_strike_day, scored_minutes)), 0) as scored_minutes,
        coalesce(sum(if(not is_strike_day, good_service_minutes)), 0) as good_service_minutes,
        coalesce(sum(if(not is_strike_day, weighted_disruption_minutes)), 0)
            as weighted_disruption_minutes,
        coalesce(sum(if(not is_strike_day, time_weighted_scored_minutes)), 0)
            as time_weighted_scored_minutes
    from daily
    group by line_id, week_start
),

scored as (
    select
        *,
        max(strike_days) over (partition by week_start) > 0 as week_has_industrial_action,
        {{ disruption_index('weighted_disruption_minutes', 'time_weighted_scored_minutes') }}
            as disruption_index,
        {{ percent_of('good_service_minutes', 'scored_minutes') }} as good_service_pct,
        if(scheduled_minutes > 0, 1e0 * observed_minutes / scheduled_minutes) as completeness,
        if(scheduled_minutes > 0, 1e0 * engineering_works_minutes / scheduled_minutes)
            as engineering_works_share
    from weekly
),

flagged as (
    select
        *,
        case
            when
                engineering_works_share > 1 - {{ var('min_open_share_to_rank') }}
                then 'engineering_works'
            when disruption_index is null then 'no_scored_minutes'
        end as unranked_reason
    from scored
)

select
    line_id,
    week_start,
    date_add('day', 6, week_start) as week_end,
    strike_days,
    week_has_industrial_action,
    scheduled_minutes,
    observed_minutes,
    engineering_works_minutes,
    scored_minutes,
    good_service_minutes,
    weighted_disruption_minutes,
    time_weighted_scored_minutes,
    disruption_index,
    good_service_pct,
    completeness,
    coalesce(completeness, 0) < {{ var('provisional_completeness') }} as is_provisional,
    unranked_reason,
    unranked_reason is null as is_ranked,
    if(
        unranked_reason is null,
        rank() over (
            partition by week_start, unranked_reason is null
            order by disruption_index asc, good_service_pct desc nulls last
        )
    ) as disruption_rank,
    {{ row_key(['line_id', 'week_start']) }} as line_weekly_key
from flagged
