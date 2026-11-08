-- Over the last 7 days, every spine minute has exactly one row per in-scope
-- line, so a minute with no poll is a missing row and never just absent.
-- The check stops at the newest minute built: int_line_minute_status ends
-- at the latest staged status poll, which can trail the spine by a minute.
with actual as (
    select
        line_id,
        minute_utc,
        count(*) as line_minute_rows
    from {{ ref('int_line_minute_status') }}
    where dt >= current_date - interval '7' day
    group by line_id, minute_utc
),

newest_built as (
    select max(minute_utc) as newest_minute
    from actual
),

expected as (
    select
        in_scope_lines.line_id,
        spine.minute_utc
    from {{ ref('int_utc_minutes') }} as spine
    cross join {{ ref('dim_lines') }} as in_scope_lines
    cross join newest_built
    where
        spine.dt >= current_date - interval '7' day
        and spine.minute_utc <= newest_built.newest_minute
)

select
    expected.line_id,
    expected.minute_utc,
    coalesce(actual.line_minute_rows, 0) as line_minute_rows
from expected
left join actual
    on
        expected.line_id = actual.line_id
        and expected.minute_utc = actual.minute_utc
where coalesce(actual.line_minute_rows, 0) <> 1
