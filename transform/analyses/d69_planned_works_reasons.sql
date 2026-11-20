-- D69 audit: the suspension and delay entries since a date, by how TfL files
-- them and what their reason says, with a shortened example of each reason.
-- Shows planned works under unplanned-looking codes, overruns that must keep
-- counting, mixed messages (an incident and works in separate sentences) and
-- planned-works wording the patterns miss. The reason is read sentence by
-- sentence, as int_line_minute_status does:
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

sentences as (
    select
        entries.line_id,
        entries.status_severity_description,
        entries.disruption_category,
        entries.poll_minute,
        entries.reason,
        entries.reason_lower,
        parts.sentence_number,
        parts.sentence_text
    from entries
    -- The same split as int_line_minute_status.
    cross join
        unnest(regexp_split(entries.reason_lower, '[.](?![0-9])'))
        with ordinality as parts (sentence_text, sentence_number)
),

sentence_signals as (
    select
        sentences.line_id,
        sentences.status_severity_description,
        sentences.disruption_category,
        sentences.poll_minute,
        sentences.reason,
        sentences.reason_lower,
        sentences.sentence_number,
        coalesce(bool_or(patterns.signal = 'planned_works'), false) as says_planned_works,
        coalesce(bool_or(patterns.signal = 'overrun'), false) as says_overrun,
        coalesce(bool_or(patterns.signal = 'disruption'), false) as says_disruption
    from sentences
    left join patterns
        on regexp_like(sentences.sentence_text, patterns.pattern)
    group by
        sentences.line_id,
        sentences.status_severity_description,
        sentences.disruption_category,
        sentences.poll_minute,
        sentences.reason,
        sentences.reason_lower,
        sentences.sentence_number
),

signals as (
    select
        line_id,
        status_severity_description,
        disruption_category,
        poll_minute,
        reason,
        bool_or(says_planned_works) as says_planned_works,
        bool_or(says_overrun) as says_overrun,
        bool_and(says_planned_works or not says_disruption) as every_disruption_is_works,
        -- Wording near "works" that no pattern catches, for review.
        regexp_like(reason_lower, 'work') as mentions_work
    from sentence_signals
    group by
        line_id,
        status_severity_description,
        disruption_category,
        poll_minute,
        reason,
        reason_lower
)

select
    line_id,
    status_severity_description,
    disruption_category,
    says_planned_works,
    says_overrun,
    every_disruption_is_works,
    mentions_work,
    -- What the model makes of a real-time entry: reason_text when this is
    -- true. A PlannedWork entry is planned_category unless it says overrun.
    (says_planned_works and every_disruption_is_works and not says_overrun) as reason_text_basis,
    count(distinct poll_minute) as poll_minutes,
    substr(arbitrary(reason), 1, 160) as example_reason
from signals
group by
    line_id,
    status_severity_description,
    disruption_category,
    says_planned_works,
    says_overrun,
    every_disruption_is_works,
    mentions_work
order by poll_minutes desc
