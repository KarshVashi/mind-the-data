{{ config(severity=var('gate_test_severity')) }}

-- PLAN M4: fails when more than `gate_max_out_of_band_share` of core-station
-- hours on measured Tube lines fall outside the quality gate (inferred
-- trains between `gate_low_ratio` and `gate_high_ratio` of the timetable).
-- Judged over the last seven complete London days, on observed hours only:
-- a poller gap is missing data, not a failed hour.
--
-- Severity comes from the var `gate_test_severity`, `warn` until the M4
-- acceptance passes and the owner settles D44; a failing gate then shows in
-- every build without stopping the daily transform.

with recent as (
    select
        hours.line_id,
        hours.is_in_band
    from {{ ref('int_measured_hours') }} as hours
    inner join {{ ref('lines') }} as in_scope_lines
        on hours.line_id = in_scope_lines.line_id
    where
        in_scope_lines.mode_name = 'tube'
        and hours.is_line_measured
        and hours.role = 'core'
        and hours.is_observed
        and hours.london_date
        >= date_add(
            'day',
            -7,
            cast(
                {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
                as date
            )
        )
        and hours.london_date
        < cast(
            {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
            as date
        )
),

summary as (
    select
        count(*) as observed_hours,
        count_if(not is_in_band) as out_of_band_hours
    from recent
)

select
    observed_hours,
    out_of_band_hours,
    1e0 * out_of_band_hours / nullif(observed_hours, 0) as out_of_band_share
from summary
-- No observed hour at all (a collector outage, or every line switched
-- off) is a failure too, not a silent pass.
where
    observed_hours = 0
    or 1e0 * out_of_band_hours / observed_hours > {{ var('gate_max_out_of_band_share') }}
