{{ config(materialized='table') }}

-- The season table (PLAN 6.2, M6; DECISIONS.md D58), one row per line and
-- season. A season runs from 1 April to 31 March (London dates) and resets
-- each April; `season` is labelled like 2026-27. The first season starts
-- with the collector, so until April 2027 it is the season to date.
--
-- Standings are the declared view over every London date of the season so
-- far, with the weekly table's rules: ratios from summed minutes, the
-- Disruption Index as the time-weighted mean severity (D19), lowest first,
-- ties to the higher Good Service %, still-equal lines sharing a rank
-- (D20), and lines that ran under `min_open_share_to_rank` of their
-- scheduled minutes, or have no scored minutes, unranked (D22). Dates
-- before the collector's first observed minute are left out.

with collection as (
    -- Days before the collector's first observed minute carry scheduled
    -- minutes and nothing else; they would only drag completeness down.
    select min(london_date) as first_collected_date
    from {{ ref('fct_line_daily') }}
    where observed_minutes > 0
),

daily as (
    select
        daily.*,
        if(
            month(daily.london_date) >= 4,
            year(daily.london_date),
            year(daily.london_date) - 1
        ) as season_year
    from {{ ref('fct_line_daily') }} as daily
    inner join {{ ref('dim_lines') }} as in_scope_lines
        on daily.line_id = in_scope_lines.line_id
    inner join collection
        on daily.london_date >= collection.first_collected_date
),

seasons as (
    select
        line_id,
        season_year,
        min(if(observed_minutes > 0, london_date)) as first_observed_date,
        max(if(observed_minutes > 0, london_date)) as last_observed_date,
        sum(scheduled_minutes) as scheduled_minutes,
        sum(observed_minutes) as observed_minutes,
        sum(engineering_works_minutes) as engineering_works_minutes,
        sum(scored_minutes) as scored_minutes,
        sum(good_service_minutes) as good_service_minutes,
        sum(weighted_disruption_minutes) as weighted_disruption_minutes,
        sum(time_weighted_scored_minutes) as time_weighted_scored_minutes,
        sum(incidents) as incidents
    from daily
    group by line_id, season_year
),

scored as (
    select
        *,
        {{ disruption_index('weighted_disruption_minutes', 'time_weighted_scored_minutes') }}
            as disruption_index,
        {{ percent_of('good_service_minutes', 'scored_minutes') }} as good_service_pct,
        {{ per_100_hours('incidents', 'scored_minutes') }} as incidents_per_100_hours,
        if(scheduled_minutes > 0, 1e0 * observed_minutes / scheduled_minutes) as completeness,
        if(scheduled_minutes > 0, 1e0 * engineering_works_minutes / scheduled_minutes)
            as engineering_works_share
    from seasons
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
    cast(season_year as varchar) || '-' || lpad(cast(mod(season_year + 1, 100) as varchar), 2, '0')
        as season,
    date(cast(season_year as varchar) || '-04-01') as season_start,
    date(cast(season_year + 1 as varchar) || '-03-31') as season_end,
    first_observed_date,
    last_observed_date,
    scheduled_minutes,
    observed_minutes,
    engineering_works_minutes,
    scored_minutes,
    good_service_minutes,
    weighted_disruption_minutes,
    time_weighted_scored_minutes,
    incidents,
    disruption_index,
    good_service_pct,
    incidents_per_100_hours,
    completeness,
    coalesce(completeness, 0) < {{ var('provisional_completeness') }} as is_provisional,
    engineering_works_share,
    unranked_reason,
    unranked_reason is null as is_ranked,
    if(
        unranked_reason is null,
        rank() over (
            partition by season_year, unranked_reason is null
            order by disruption_index asc, good_service_pct desc nulls last
        )
    ) as season_rank,
    date(cast(season_year + 1 as varchar) || '-04-01')
    <= cast(
        {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
        as date
    ) as is_season_complete,
    {{ row_key(['line_id', 'season_year']) }} as line_season_key
from flagged
