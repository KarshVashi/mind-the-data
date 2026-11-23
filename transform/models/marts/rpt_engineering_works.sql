{{ config(materialized='table') }}

-- PLAN 7.4 Engineering Works table, per line and ISO week (M6, D50).
-- Minutes whose worst declared status is a closure, or planned works under
-- a suspension or delay code (D69), leave the Disruption Index and are
-- reported here instead. A Part Closure takes the whole line's
-- minute (ADR-004), so a branch closure counts in full. Lines without works
-- are kept with 0 hours, so every line-week has a row.

with weekly as (
    select
        line_id,
        week_start,
        week_end,
        scheduled_minutes,
        engineering_works_minutes,
        engineering_works_share,
        unranked_reason,
        is_week_complete
    from {{ ref('fct_line_weekly') }}
)

select
    line_id,
    week_start,
    week_end,
    scheduled_minutes,
    engineering_works_minutes,
    engineering_works_minutes / 60e0 as engineering_works_hours,
    engineering_works_share,
    coalesce(unranked_reason = 'engineering_works', false) as is_unranked_for_works,
    is_week_complete,
    -- Most hours closed first; lines without works share the last place.
    rank() over (
        partition by week_start order by engineering_works_minutes desc
    ) as works_rank,
    {{ row_key(['line_id', 'week_start']) }} as engineering_works_key
from weekly
