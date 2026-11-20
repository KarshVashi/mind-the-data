{{ config(materialized='table') }}

-- The cause TfL names in the status text of each disrupted minute (D74).
-- It is the cause as named, not the root cause: TfL's wording is the only
-- source, and it names one cause at a time.
--
-- Rule: of the causes in seed disruption_cause_patterns that match the
-- lower-cased reason, the one whose match starts first wins. Equal starts go
-- to the lower priority number. A reason matching none is 'No cause given',
-- as is a missing or empty one.
--
-- One row per line-minute that is scored and not Good Service (so never a
-- missing minute, an engineering-works minute or a Service Closed one), with
-- the status int_line_minute_status chose for it (ADR-004). The text is
-- classified once per distinct reason, then joined back.

with disrupted as (
    select
        line_id,
        minute_utc,
        london_date,
        week_start,
        status_severity,
        status_severity_description,
        is_planned,
        severity_weight,
        reason,
        dt,
        line_minute_key,
        -- Whitespace folded, a space at each end so every pattern's leading
        -- boundary has a character to consume and match starts are comparable.
        concat(
            ' ',
            lower(trim(regexp_replace(replace(coalesce(reason, ''), '|', ' '), '\s+', ' '))),
            ' '
        ) as reason_text
    from {{ ref('int_line_minute_status') }}
    where is_scored and status_severity <> 10
),

texts as (
    select distinct reason_text
    from disrupted
),

matches as (
    select
        texts.reason_text,
        patterns.cause,
        patterns.priority,
        regexp_position(texts.reason_text, patterns.pattern) as match_start
    from texts
    cross join {{ ref('disruption_cause_patterns') }} as patterns
),

first_cause as (
    select
        reason_text,
        cause
    from (
        select
            reason_text,
            cause,
            row_number() over (
                partition by reason_text order by match_start asc, priority asc
            ) as cause_rank
        from matches
        -- regexp_position gives -1 for no match.
        where match_start > 0
    ) as ranked_causes
    where cause_rank = 1
)

select
    disrupted.line_id,
    disrupted.minute_utc,
    disrupted.london_date,
    disrupted.week_start,
    disrupted.status_severity,
    disrupted.status_severity_description,
    disrupted.is_planned,
    disrupted.severity_weight,
    disrupted.reason,
    disrupted.dt,
    disrupted.line_minute_key,
    -- Suspended (2) or Part Suspended (3): TfL's "no service" statuses, which
    -- are mostly part-line. Part Suspended is most of them.
    disrupted.status_severity in (2, 3) as is_suspended,
    coalesce(first_cause.cause, 'No cause given') as cause,
    first_cause.cause is not null as is_cause_named
from disrupted
left join first_cause
    on disrupted.reason_text = first_cause.reason_text
