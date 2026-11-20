-- Exploratory (D78, ADR-020): not built in prod and read by nothing published.
{{ config(materialized='table', tags=['exploratory'], enabled=target.name != 'prod') }}

-- The declared league weighted by typical demand instead of the clock
-- (D78). Each scored minute's severity weight is multiplied by the typical
-- number of passengers boarding the line in that minute (NUMBAT, ADR-020),
-- in place of the time weight: demand already says which minutes are busy.
-- Missing minutes leave both numerator and denominator, as in D19, and are
-- never read as Good Service; the share of the week's typical passengers
-- whose minutes were observed sits beside each line.

with minutes as (
    select
        line_id,
        week_start,
        london_date,
        is_missing,
        is_scored,
        severity_weight,
        hour(london_ts) as hour_of_day
    from {{ ref('int_line_minute_status') }}
    where is_scheduled
),

-- A NUMBAT traffic day runs 05:00 to 04:59, so a minute before 05:00
-- belongs to the previous London date's day type (Friday night's Night
-- Tube is Friday's demand).
service_days as (
    select
        minutes.*,
        day_types.day_type as service_day_type
    from minutes
    inner join {{ ref('day_types') }} as day_types
        on
            day_of_week(
                if(
                    minutes.hour_of_day < 5,
                    date_add('day', -1, minutes.london_date),
                    minutes.london_date
                )
            )
            = day_types.iso_day_of_week
),

line_demand as (
    select
        line_map.line_id,
        demand.service_day_type,
        demand.hour_of_day,
        sum(line_map.demand_share * demand.boarders) / 60e0 as boarders_per_minute,
        sum(line_map.demand_share * demand.link_load_sum) / 60e0 as link_load_per_minute
    from {{ ref('seed_line_demand') }} as demand
    inner join {{ ref('seed_demand_line_map') }} as line_map
        on demand.numbat_line = line_map.numbat_line
    group by line_map.line_id, demand.service_day_type, demand.hour_of_day
),

weighted_minutes as (
    select
        service_days.*,
        line_demand.boarders_per_minute,
        line_demand.link_load_per_minute
    from service_days
    left join line_demand
        on
            service_days.line_id = line_demand.line_id
            and service_days.service_day_type = line_demand.service_day_type
            and service_days.hour_of_day = line_demand.hour_of_day
),

weekly as (
    select
        line_id,
        week_start,
        count(*) as scheduled_minutes,
        count_if(boarders_per_minute is null) as minutes_without_demand,
        sum(boarders_per_minute) as scheduled_boarders,
        sum(if(not is_missing, boarders_per_minute)) as observed_boarders,
        sum(if(is_scored, boarders_per_minute)) as scored_boarders,
        -- An indicator times demand, so a line with no demand stays null.
        sum(if(is_scored, boarders_per_minute * if(severity_weight > 0, 1, 0)))
            as disrupted_boarders,
        sum(if(is_scored, severity_weight * boarders_per_minute))
            as passenger_disruption_minutes,
        sum(if(is_scored, link_load_per_minute)) as scored_link_loads,
        sum(if(is_scored, severity_weight * link_load_per_minute))
            as link_load_disruption_minutes
    from weighted_minutes
    group by line_id, week_start
),

declared as (
    select
        line_id,
        week_start,
        disruption_index,
        disruption_rank,
        is_ranked,
        unranked_reason,
        is_provisional,
        is_week_complete,
        completeness
    from {{ ref('fct_line_weekly') }}
),

scored as (
    select
        weekly.*,
        declared.disruption_index,
        declared.disruption_rank,
        declared.is_provisional,
        declared.is_week_complete,
        declared.completeness,
        if(
            weekly.scheduled_boarders > 0,
            weekly.observed_boarders / weekly.scheduled_boarders
        ) as demand_completeness,
        -- Demand-weighted mean severity x 100, 0 to 800: the Disruption
        -- Index with passengers in place of the time weight.
        if(
            weekly.scored_boarders > 0,
            100 * weekly.passenger_disruption_minutes / weekly.scored_boarders
        ) as passenger_disruption_index,
        if(
            weekly.scored_link_loads > 0,
            100 * weekly.link_load_disruption_minutes / weekly.scored_link_loads
        ) as link_load_disruption_index,
        case
            -- The declared league's reasons come first (D22), so a line
            -- closed for works stays unranked here too.
            when declared.line_id is null then 'not_in_declared_league'
            when not declared.is_ranked then declared.unranked_reason
            when coalesce(weekly.scheduled_boarders, 0) = 0 then 'no_demand_data'
            when coalesce(weekly.scored_boarders, 0) = 0 then 'no_scored_demand'
        end as unranked_reason
    from weekly
    left join declared
        on
            weekly.line_id = declared.line_id
            and weekly.week_start = declared.week_start
),

ranked as (
    select
        *,
        unranked_reason is null as is_ranked,
        -- Each line as its typical passenger saw it: lowest index first.
        if(
            unranked_reason is null,
            rank() over (
                partition by week_start, unranked_reason is null
                order by passenger_disruption_index asc nulls last
            )
        ) as passenger_index_rank,
        -- Network impact: fewest severity-weighted passenger-minutes first.
        -- Large lines rank lower for the same disruption, by design.
        if(
            unranked_reason is null,
            rank() over (
                partition by week_start, unranked_reason is null
                order by passenger_disruption_minutes asc nulls last
            )
        ) as passenger_impact_rank,
        if(
            unranked_reason is null,
            rank() over (
                partition by week_start, unranked_reason is null
                order by link_load_disruption_minutes asc nulls last
            )
        ) as link_load_impact_rank
    from scored
)

select
    line_id,
    week_start,
    scheduled_minutes,
    minutes_without_demand,
    scheduled_boarders,
    observed_boarders,
    scored_boarders,
    disrupted_boarders,
    passenger_disruption_minutes,
    scored_link_loads,
    link_load_disruption_minutes,
    demand_completeness,
    completeness,
    is_provisional,
    is_week_complete,
    disruption_index,
    disruption_rank,
    passenger_disruption_index,
    link_load_disruption_index,
    is_ranked,
    unranked_reason,
    passenger_index_rank,
    passenger_impact_rank,
    link_load_impact_rank,
    date_add('day', 6, week_start) as week_end,
    passenger_index_rank - disruption_rank as index_rank_shift,
    passenger_impact_rank - disruption_rank as impact_rank_shift,
    {{ row_key(['line_id', 'week_start']) }} as line_weekly_key
from ranked
