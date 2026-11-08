{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='line_minute_key',
        partitioned_by=['dt'],
        incremental_predicates=staging_incremental_predicates(),
        on_schema_change='fail',
    )
}}

-- One row per in-scope line per UTC minute, whether or not a poll landed.
-- The worst current status wins (ADR-004). A minute can hold one or more
-- polls (smoke-test polls, retries): the latest successful poll that lists
-- the line is read. A minute with no such poll, or whose poll has no current
-- entry for the line, is missing and never Good Service.

-- The spine stops at the latest staged status poll. int_utc_minutes runs
-- after staging in a build, so its last minutes can be newer than anything
-- staged yet; they would read as missing until the next run. With no
-- staged poll in the window (an outage), every minute is kept, as missing.
with latest_staged as (
    select max(poll_minute) as latest_poll_minute
    from {{ ref('stg_line_status') }}
    where {{ dt_in_window('dt') }}
),

minutes as (
    select
        spine.minute_utc,
        spine.dt,
        {{ london_local('spine.minute_utc') }} as london_ts
    from {{ ref('int_utc_minutes') }} as spine
    cross join latest_staged
    where
        {{ dt_in_window('spine.dt') }}
        and (
            latest_staged.latest_poll_minute is null
            or spine.minute_utc <= latest_staged.latest_poll_minute
        )
),

calendar as (
    select
        minute_utc,
        dt,
        london_ts,
        cast(london_ts as date) as london_date,
        hour(london_ts) * 60 + minute(london_ts) as london_minute_of_day,
        day_of_week(london_ts) as iso_day_of_week
    from minutes
),

banded as (
    select
        calendar.minute_utc,
        calendar.dt,
        calendar.london_ts,
        calendar.london_date,
        calendar.london_minute_of_day,
        today.day_type,
        today.day_class,
        previous_day.day_type as previous_day_type,
        bands.time_band,
        bands.time_weight,
        date_trunc('week', calendar.london_date) as week_start
    from calendar
    inner join {{ ref('day_types') }} as today
        on calendar.iso_day_of_week = today.iso_day_of_week
    -- The previous calendar day, whose service can run past midnight.
    inner join {{ ref('day_types') }} as previous_day
        on (calendar.iso_day_of_week + 5) % 7 + 1 = previous_day.iso_day_of_week
    inner join {{ ref('time_bands') }} as bands
        on
            today.day_class = bands.day_class
            and calendar.london_minute_of_day >= bands.band_start_minute
            and calendar.london_minute_of_day < bands.band_end_minute
),

line_minutes as (
    select
        in_scope_lines.line_id,
        banded.minute_utc,
        banded.dt,
        banded.london_ts,
        banded.london_date,
        banded.week_start,
        banded.day_type,
        banded.day_class,
        banded.time_band,
        banded.time_weight,
        -- In service if this London day's hours cover the minute, or the
        -- previous day's hours run past midnight into it.
        (
            same_day_hours.line_id is not null or overnight_hours.line_id is not null
        ) as is_scheduled
    from banded
    cross join {{ ref('dim_lines') }} as in_scope_lines
    left join {{ ref('line_operating_hours') }} as same_day_hours
        on
            in_scope_lines.line_id = same_day_hours.line_id
            and banded.day_type = same_day_hours.day_type
            and banded.london_minute_of_day >= same_day_hours.service_start_minute
            and banded.london_minute_of_day < same_day_hours.service_end_minute
    left join {{ ref('line_operating_hours') }} as overnight_hours
        on
            in_scope_lines.line_id = overnight_hours.line_id
            and banded.previous_day_type = overnight_hours.day_type
            and banded.london_minute_of_day + 1440 >= overnight_hours.service_start_minute
            and banded.london_minute_of_day + 1440 < overnight_hours.service_end_minute
),

successful_polls as (
    select
        poll_minute,
        count(distinct poll_id) as successful_polls
    from {{ ref('stg_polls') }}
    where
        feed = 'line_status'
        and is_ok
        and {{ dt_in_window('dt') }}
    group by poll_minute
),

entries as (
    select
        poll_id,
        request_path,
        polled_at,
        poll_minute,
        line_id,
        status_ordinal,
        status_severity,
        status_severity_description,
        reason,
        is_planned,
        valid_from,
        valid_to
    from {{ ref('stg_line_status') }}
    where {{ dt_in_window('dt') }}
),

line_polls as (
    select
        line_id,
        poll_minute,
        poll_id,
        request_path,
        row_number() over (
            partition by line_id, poll_minute
            order by polled_at desc, poll_id desc, request_path desc
        ) as poll_rank
    from (
        select distinct
            line_id,
            poll_minute,
            poll_id,
            request_path,
            polled_at
        from entries
    ) as distinct_line_polls
),

-- An entry is current when it has no validity periods, or one of its
-- periods overlaps the minute. One row per entry, however many periods.
current_entries as (
    select
        entries.line_id,
        entries.poll_minute,
        entries.poll_id,
        entries.status_ordinal,
        entries.status_severity,
        entries.status_severity_description,
        entries.reason,
        entries.is_planned
    from entries
    inner join line_polls
        on
            entries.line_id = line_polls.line_id
            and entries.poll_minute = line_polls.poll_minute
            and entries.poll_id = line_polls.poll_id
            and entries.request_path = line_polls.request_path
            and line_polls.poll_rank = 1
    where
        (
            entries.valid_from is null
            or entries.valid_from < date_add('minute', 1, entries.poll_minute)
        )
        and (entries.valid_to is null or entries.valid_to > entries.poll_minute)
    group by
        entries.line_id,
        entries.poll_minute,
        entries.poll_id,
        entries.status_ordinal,
        entries.status_severity,
        entries.status_severity_description,
        entries.reason,
        entries.is_planned
),

weighted as (
    select
        current_entries.line_id,
        current_entries.poll_minute,
        current_entries.poll_id,
        current_entries.status_ordinal,
        current_entries.status_severity,
        current_entries.status_severity_description,
        current_entries.reason,
        current_entries.is_planned,
        weights.is_closure,
        weights.is_service_closed,
        -- Null for a code missing from the seed; it then wins the minute
        -- below and fails the not_null test instead of passing as 0.
        if(current_entries.is_planned, weights.weight_planned, weights.weight_unplanned)
            as severity_weight
    from current_entries
    left join {{ ref('severity_weights') }} as weights
        on current_entries.status_severity = weights.severity_code
),

ranked as (
    select
        *,
        row_number() over (
            partition by line_id, poll_minute
            order by
                severity_weight desc nulls first,
                status_severity asc,
                reason asc nulls first,
                status_ordinal asc
        ) as status_rank,
        bool_or(coalesce(is_closure, false)) over (
            partition by line_id, poll_minute
        ) as has_closure_entry,
        count(*) over (partition by line_id, poll_minute) as current_entries
    from weighted
),

worst_status as (
    select *
    from ranked
    where status_rank = 1
),

joined as (
    select
        line_minutes.line_id,
        line_minutes.minute_utc,
        line_minutes.london_ts,
        line_minutes.london_date,
        line_minutes.week_start,
        line_minutes.day_type,
        line_minutes.day_class,
        line_minutes.time_band,
        line_minutes.time_weight,
        line_minutes.is_scheduled,
        worst_status.poll_id as status_poll_id,
        worst_status.status_severity,
        worst_status.status_severity_description,
        worst_status.reason,
        worst_status.is_planned,
        worst_status.severity_weight,
        line_minutes.dt,
        coalesce(successful_polls.successful_polls, 0) as successful_polls,
        coalesce(worst_status.current_entries, 0) as current_entries,
        coalesce(worst_status.has_closure_entry, false) as has_closure_entry,
        worst_status.poll_id is null as is_missing,
        case
            when worst_status.poll_id is not null then null
            when successful_polls.poll_minute is null then 'no_successful_poll'
            when listed.poll_id is null then 'line_not_in_poll'
            else 'no_current_entry'
        end as missing_reason,
        coalesce(worst_status.is_closure, false) as is_engineering_works,
        coalesce(worst_status.is_service_closed, false) as is_service_closed
    from line_minutes
    left join successful_polls
        on line_minutes.minute_utc = successful_polls.poll_minute
    left join line_polls as listed
        on
            line_minutes.line_id = listed.line_id
            and line_minutes.minute_utc = listed.poll_minute
            and listed.poll_rank = 1
    left join worst_status
        on
            line_minutes.line_id = worst_status.line_id
            and line_minutes.minute_utc = worst_status.poll_minute
)

select
    line_id,
    minute_utc,
    london_ts,
    london_date,
    week_start,
    day_type,
    day_class,
    time_band,
    time_weight,
    is_scheduled,
    successful_polls,
    status_poll_id,
    status_severity,
    status_severity_description,
    reason,
    is_planned,
    severity_weight,
    current_entries,
    has_closure_entry,
    is_missing,
    missing_reason,
    is_engineering_works,
    is_service_closed,
    -- Counts toward the Disruption Index and Good Service %: in service,
    -- observed, and neither engineering works nor TfL's Service Closed.
    (
        is_scheduled and not is_missing and not is_engineering_works and not is_service_closed
    ) as is_scored,
    dt,
    {{ row_key(['line_id', 'minute_utc']) }} as line_minute_key
from joined
