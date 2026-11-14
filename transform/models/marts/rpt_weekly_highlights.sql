{{ config(materialized='table') }}

-- The week's three headline facts (M6), one row per ISO week:
--
-- Wooden Spoon (D48): the bottom-ranked line. Lines sharing the bottom rank
-- are split by line id, and `wooden_spoon_shared_by` says how many share it.
--
-- Line of the Week (D47): the largest fall in Disruption Index since the
-- previous week, among lines ranked and not provisional in both weeks; ties
-- go to the lower index this week, then line id. Null, with a reason, when
-- the previous week is missing or incomplete (`no_previous_week`,
-- `previous_week_incomplete`) or no line improved (`no_line_improved`). A
-- previous week is complete once it has ended and at least
-- `provisional_completeness` of its scheduled minutes were observed.
--
-- Single worst incident (D48): the incident starting in the week with the
-- most severity-weighted minutes (int_incidents), ties to the longer, then
-- the earlier.

with weekly as (
    select
        line_id,
        week_start,
        observed_minutes,
        scheduled_minutes,
        disruption_index,
        disruption_rank,
        is_ranked,
        is_provisional,
        is_week_complete
    from {{ ref('fct_line_weekly') }}
),

weeks as (
    select
        week_start,
        sum(observed_minutes) as observed_minutes,
        sum(scheduled_minutes) as scheduled_minutes,
        bool_and(is_week_complete) as is_week_complete
    from weekly
    group by week_start
),

spoons as (
    select
        week_start,
        line_id,
        disruption_index,
        is_provisional,
        count(*) over (partition by week_start, disruption_rank) as shared_by,
        row_number() over (
            partition by week_start order by disruption_rank desc, line_id asc
        ) as spoon_position
    from weekly
    where is_ranked
),

improvements as (
    select
        this_week.week_start,
        this_week.line_id,
        this_week.disruption_index,
        previous_week.disruption_index as previous_disruption_index,
        previous_week.disruption_index - this_week.disruption_index as index_improvement
    from weekly as this_week
    inner join weekly as previous_week
        on
            this_week.line_id = previous_week.line_id
            and previous_week.week_start = date_add('day', -7, this_week.week_start)
    where
        this_week.is_ranked
        and not this_week.is_provisional
        and previous_week.is_ranked
        and not previous_week.is_provisional
        and previous_week.disruption_index > this_week.disruption_index
),

best_improvement as (
    select
        *,
        row_number() over (
            partition by week_start
            order by index_improvement desc, disruption_index asc, line_id asc
        ) as improvement_position
    from improvements
),

incidents as (
    select
        start_week_start as week_start,
        line_id,
        started_at,
        duration_minutes,
        severity_weighted_minutes,
        worst_status_description,
        is_open,
        row_number() over (
            partition by start_week_start
            order by
                severity_weighted_minutes desc, duration_minutes desc, started_at asc, line_id asc
        ) as incident_position
    from {{ ref('int_incidents') }}
)

select
    weeks.week_start,
    date_add('day', 6, weeks.week_start) as week_end,
    weeks.is_week_complete,
    spoons.line_id as wooden_spoon_line_id,
    spoons.disruption_index as wooden_spoon_disruption_index,
    spoons.is_provisional as wooden_spoon_is_provisional,
    spoons.shared_by as wooden_spoon_shared_by,
    best_improvement.line_id as line_of_the_week_line_id,
    best_improvement.disruption_index as line_of_the_week_disruption_index,
    best_improvement.previous_disruption_index as line_of_the_week_previous_index,
    best_improvement.index_improvement as line_of_the_week_improvement,
    case
        when best_improvement.line_id is not null then null
        when previous_weeks.week_start is null or previous_weeks.observed_minutes = 0
            then 'no_previous_week'
        when
            not previous_weeks.is_week_complete
            or 1e0 * previous_weeks.observed_minutes / previous_weeks.scheduled_minutes
            < {{ var('provisional_completeness') }}
            then 'previous_week_incomplete'
        else 'no_line_improved'
    end as line_of_the_week_null_reason,
    incidents.line_id as worst_incident_line_id,
    incidents.started_at as worst_incident_started_at,
    incidents.duration_minutes as worst_incident_duration_minutes,
    incidents.severity_weighted_minutes as worst_incident_severity_weighted_minutes,
    incidents.worst_status_description as worst_incident_worst_status,
    incidents.is_open as worst_incident_is_open
from weeks
left join weeks as previous_weeks
    on previous_weeks.week_start = date_add('day', -7, weeks.week_start)
left join spoons
    on
        weeks.week_start = spoons.week_start
        and spoons.spoon_position = 1
left join best_improvement
    on
        weeks.week_start = best_improvement.week_start
        and best_improvement.improvement_position = 1
        and previous_weeks.is_week_complete
        and 1e0 * previous_weeks.observed_minutes / previous_weeks.scheduled_minutes
        >= {{ var('provisional_completeness') }}
left join incidents
    on
        weeks.week_start = incidents.week_start
        and incidents.incident_position = 1
