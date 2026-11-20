{{ config(materialized='table') }}

-- What TfL says goes wrong (D74): per ISO week and cause, the scored
-- non-Good-Service minutes whose status text names that cause first. One row
-- per line and cause, and one per cause for the network (line_id 'network',
-- all lines' minutes together). Only causes with minutes get a row.
-- Shares are of that line's (or the network's) disrupted and suspended
-- minutes that week; the suspended share is null when it had none.

with by_line as (
    select
        week_start,
        line_id,
        cause,
        count(*) as minutes,
        count_if(is_suspended) as suspended_minutes,
        sum(severity_weight) as weighted_minutes
    from {{ ref('int_status_causes') }}
    group by week_start, line_id, cause
),

scopes as (
    select
        week_start,
        line_id,
        false as is_network,
        cause,
        minutes,
        suspended_minutes,
        weighted_minutes
    from by_line

    union all

    select
        week_start,
        'network' as line_id,
        true as is_network,
        cause,
        sum(minutes) as minutes,
        sum(suspended_minutes) as suspended_minutes,
        sum(weighted_minutes) as weighted_minutes
    from by_line
    group by week_start, cause
),

shares as (
    select
        *,
        sum(minutes) over (partition by week_start, line_id) as disrupted_minutes,
        sum(suspended_minutes) over (partition by week_start, line_id) as scope_suspended_minutes
    from scopes
)

select
    week_start,
    date_add('day', 6, week_start) as week_end,
    line_id,
    is_network,
    cause,
    cause = 'No cause given' as is_no_cause,
    minutes,
    disrupted_minutes,
    1e0 * minutes / disrupted_minutes as share_of_disrupted,
    suspended_minutes,
    scope_suspended_minutes,
    if(scope_suspended_minutes > 0, 1e0 * suspended_minutes / scope_suspended_minutes)
        as share_of_suspended,
    weighted_minutes,
    -- A week is complete once its last London minute is in the data.
    date_add('day', 7, week_start)
    <= cast(
        {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
        as date
    )
        as is_week_complete,
    {{ row_key(['week_start', 'line_id', 'cause']) }} as disruption_cause_key
from shares
