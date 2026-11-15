{{ config(materialized='table') }}

-- The Recovery Time table (PLAN M6; DECISIONS.md D59), one row per line and
-- ISO week. PROVISIONAL: it rests on D18, which is still open. Incidents
-- and recovery follow int_incidents as drafted under D18: an incident
-- belongs to the week it starts in, recovery is the minutes from its first
-- to its last incident minute, and an incident still open has no recovery
-- and is left out of the median.
--
-- Lines are ranked by median recovery, quickest first; equal medians are
-- split by fewer incidents, and still-equal lines share a rank. A line with
-- no closed incident in the week is not ranked (`no_recovered_incidents`).
-- Every line-week of fct_line_weekly has a row.

with incidents as (
    select
        line_id,
        start_week_start as week_start,
        recovery_minutes
    from {{ ref('int_incidents') }}
),

counts as (
    select
        line_id,
        week_start,
        count(*) as incidents,
        count(recovery_minutes) as recovered_incidents,
        avg(recovery_minutes) as mean_recovery_minutes,
        max(recovery_minutes) as longest_recovery_minutes
    from incidents
    group by line_id, week_start
),

-- Exact median: the mean of the middle one or two recovery times.
recovery_ranked as (
    select
        line_id,
        week_start,
        recovery_minutes,
        row_number() over (partition by line_id, week_start order by recovery_minutes)
            as recovery_position,
        count(*) over (partition by line_id, week_start) as recovered
    from incidents
    where recovery_minutes is not null
),

medians as (
    select
        line_id,
        week_start,
        avg(recovery_minutes) as median_recovery_minutes
    from recovery_ranked
    where recovery_position in ((recovered + 1) / 2, (recovered + 2) / 2)
    group by line_id, week_start
),

line_weeks as (
    select
        weekly.line_id,
        weekly.week_start,
        weekly.is_week_complete,
        medians.median_recovery_minutes,
        counts.mean_recovery_minutes,
        counts.longest_recovery_minutes,
        coalesce(counts.incidents, 0) as incidents,
        coalesce(counts.recovered_incidents, 0) as recovered_incidents,
        coalesce(counts.incidents, 0) - coalesce(counts.recovered_incidents, 0) as open_incidents
    from {{ ref('fct_line_weekly') }} as weekly
    left join counts
        on weekly.line_id = counts.line_id and weekly.week_start = counts.week_start
    left join medians
        on weekly.line_id = medians.line_id and weekly.week_start = medians.week_start
)

select
    line_id,
    week_start,
    date_add('day', 6, week_start) as week_end,
    is_week_complete,
    incidents,
    recovered_incidents,
    open_incidents,
    median_recovery_minutes,
    mean_recovery_minutes,
    longest_recovery_minutes,
    if(recovered_incidents = 0, 'no_recovered_incidents') as unranked_reason,
    if(
        recovered_incidents > 0,
        rank() over (
            partition by week_start, recovered_incidents > 0
            order by median_recovery_minutes asc, incidents asc
        )
    ) as recovery_rank,
    -- D18 is open: every number here may change when it is settled.
    'provisional_d18' as definition_status,
    {{ row_key(['line_id', 'week_start']) }} as line_weekly_key
from line_weeks
