{{ config(materialized='table') }}

-- Scored minutes per line, ISO week, worst status and time band, with no
-- weights applied (PLAN 7.5; DECISIONS.md D61). The Disruption Index under
-- any weight set is 100 x sum(minutes x severity weight x time weight) /
-- sum(minutes x time weight) over these rows, so the publish code re-ranks
-- each week under the sensitivity weight sets without reading minutes.
-- Under the base seeds it gives fct_line_weekly's index.

select
    line_id,
    week_start,
    status_severity,
    coalesce(is_planned, false) as is_planned,
    time_band,
    count(*) as scored_minutes,
    -- The base weights, so the reconstruction can be checked in SQL too.
    sum(severity_weight * time_weight) as weighted_disruption_minutes,
    sum(time_weight) as time_weighted_scored_minutes,
    {{ row_key([
        'line_id', 'week_start', 'status_severity', 'coalesce(is_planned, false)', 'time_band',
    ]) }} as status_mix_key
from {{ ref('int_line_minute_status') }}
where is_scheduled and is_scored
group by line_id, week_start, status_severity, coalesce(is_planned, false), time_band
