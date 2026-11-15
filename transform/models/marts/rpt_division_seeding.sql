{{ config(materialized='table') }}

-- Division seeding (M6, ADR-015), one row per line once seeded. The lines
-- are ordered by their Disruption Index over the first
-- `division_seeding_weeks` complete weeks, recomputed from summed minutes
-- (D19), ties to the higher Good Service %, then line id. Lines that could
-- not be ranked over those weeks (D20, D22) go last, by line id. The first
-- `division_one_size` lines start in Division One, the rest in Division
-- Two. Divisions play from the first calendar month that starts after the
-- seeding window ends.
--
-- A week is complete once every line's week has ended and at least
-- `provisional_completeness` of the network's scheduled minutes were
-- observed (the rule of D47). Until enough complete weeks exist the model
-- is empty: "not yet seeded", never an invented table. The seeding is
-- provisional while fewer than `division_full_seeding_weeks` complete
-- weeks exist.

with weeks as (
    select
        week_start,
        bool_and(is_week_complete) as is_week_complete,
        sum(observed_minutes) as observed_minutes,
        sum(scheduled_minutes) as scheduled_minutes
    from {{ ref('fct_line_weekly') }}
    group by week_start
),

complete_weeks as (
    select
        week_start,
        row_number() over (order by week_start) as week_number,
        count(*) over () as complete_weeks_available
    from weeks
    where
        is_week_complete
        and scheduled_minutes > 0
        and 1e0 * observed_minutes / scheduled_minutes >= {{ var('provisional_completeness') }}
),

seeding_window as (
    select
        min(week_start) as seeding_start,
        date_add('day', 6, max(week_start)) as seeding_end,
        count(*) as seeding_weeks,
        max(complete_weeks_available) as complete_weeks_available
    from complete_weeks
    where week_number <= {{ var('division_seeding_weeks') }}
    having count(*) = {{ var('division_seeding_weeks') }}
),

line_totals as (
    select
        weekly.line_id,
        sum(weekly.scheduled_minutes) as scheduled_minutes,
        sum(weekly.observed_minutes) as observed_minutes,
        sum(weekly.engineering_works_minutes) as engineering_works_minutes,
        sum(weekly.scored_minutes) as scored_minutes,
        sum(weekly.good_service_minutes) as good_service_minutes,
        sum(weekly.weighted_disruption_minutes) as weighted_disruption_minutes,
        sum(weekly.time_weighted_scored_minutes) as time_weighted_scored_minutes
    from {{ ref('fct_line_weekly') }} as weekly
    inner join complete_weeks
        on weekly.week_start = complete_weeks.week_start
    where complete_weeks.week_number <= {{ var('division_seeding_weeks') }}
    group by weekly.line_id
),

scored as (
    select
        line_totals.*,
        {{ disruption_index(
            'line_totals.weighted_disruption_minutes',
            'line_totals.time_weighted_scored_minutes',
        ) }} as disruption_index,
        {{ percent_of('line_totals.good_service_minutes', 'line_totals.scored_minutes') }}
            as good_service_pct,
        if(
            line_totals.scheduled_minutes > 0,
            1e0 * line_totals.observed_minutes / line_totals.scheduled_minutes
        ) as completeness,
        if(
            line_totals.scheduled_minutes > 0,
            1e0 * line_totals.engineering_works_minutes / line_totals.scheduled_minutes
        ) as engineering_works_share
    from line_totals
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
),

positioned as (
    select
        flagged.*,
        row_number() over (
            order by
                unranked_reason is not null,
                disruption_index asc nulls last,
                good_service_pct desc nulls last,
                line_id asc
        ) as seed_position
    from flagged
)

select
    positioned.line_id,
    positioned.seed_position,
    if(positioned.seed_position <= {{ var('division_one_size') }}, 1, 2) as division,
    positioned.disruption_index,
    positioned.good_service_pct,
    positioned.completeness,
    positioned.engineering_works_share,
    positioned.unranked_reason,
    seeding_window.seeding_start,
    seeding_window.seeding_end,
    seeding_window.seeding_weeks,
    seeding_window.complete_weeks_available,
    date_add('month', 1, date_trunc('month', seeding_window.seeding_end)) as first_division_month,
    if(
        seeding_window.complete_weeks_available < {{ var('division_full_seeding_weeks') }},
        'provisional',
        'confirmed'
    ) as seeding_status
from positioned
cross join seeding_window
