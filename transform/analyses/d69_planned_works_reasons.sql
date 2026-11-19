-- D69 audit: the suspension and delay entries since a date, by how TfL files
-- them and what their reason says, with a shortened example of each reason.
-- Shows planned works under unplanned-looking codes, overruns that must keep
-- counting, and planned-works wording the patterns miss:
--   dbt compile --select d69_planned_works_reasons --vars '{d69_since: "2026-11-09"}'
-- then run the compiled SQL with scripts/athena_query.py --file.

with patterns as (
    select
        signal,
        pattern
    from {{ ref('planned_works_reasons') }}
),

entries as (
    select
        statuses.line_id,
        statuses.status_severity_description,
        statuses.disruption_category,
        statuses.poll_minute,
        statuses.reason,
        lower(coalesce(statuses.reason, '')) as reason_lower
    from {{ ref('stg_line_status') }} as statuses
    where
        statuses.dt >= date '{{ var("d69_since", "2026-11-09") }}'
        -- Suspended, Part Suspended, Severe Delays (seed is_works_when_planned).
        and statuses.status_severity in (2, 3, 6)
),

signals as (
    select
        entries.line_id,
        entries.status_severity_description,
        entries.disruption_category,
        entries.poll_minute,
        entries.reason,
        coalesce(bool_or(patterns.signal = 'planned_works'), false) as says_planned_works,
        coalesce(bool_or(patterns.signal = 'overrun'), false) as says_overrun,
        -- Wording near "works" that no pattern catches, for review.
        regexp_like(entries.reason_lower, 'work') as mentions_work
    from entries
    left join patterns
        on regexp_like(entries.reason_lower, patterns.pattern)
    group by
        entries.line_id,
        entries.status_severity_description,
        entries.disruption_category,
        entries.poll_minute,
        entries.reason,
        entries.reason_lower
)

select
    line_id,
    status_severity_description,
    disruption_category,
    says_planned_works,
    says_overrun,
    mentions_work,
    count(distinct poll_minute) as poll_minutes,
    substr(arbitrary(reason), 1, 160) as example_reason
from signals
group by
    line_id,
    status_severity_description,
    disruption_category,
    says_planned_works,
    says_overrun,
    mentions_work
order by poll_minutes desc
