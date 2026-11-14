{{ config(severity=var('gate_test_severity')) }}

-- PLAN M4, as amended by ADR-014: fails when more than
-- `gate_max_out_of_band_share` of judged core-station hours on measured Tube
-- lines fall outside the quality gate (inferred trains between
-- `gate_low_ratio` and `gate_high_ratio` of the trains observed on the
-- boards). Judged over the last seven complete London days, on observed
-- hours with at least one train on the boards: a poller gap is missing
-- data, not a failed hour, and an hour with no train on the boards has
-- nothing to infer.
--
-- Severity comes from the var `gate_test_severity`, `warn` until the owner
-- settles D44 (and D46, which accepts ADR-014); a failing gate then shows
-- in every build without stopping the daily transform.

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
        and hours.is_gate_judged
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
        count(*) as judged_hours,
        count_if(not is_in_band) as out_of_band_hours
    from recent
)

select
    judged_hours,
    out_of_band_hours,
    1e0 * out_of_band_hours / nullif(judged_hours, 0) as out_of_band_share
from summary
-- No judged hour at all (a collector outage, or every line switched off)
-- is a failure too, not a silent pass.
where
    judged_hours = 0
    or 1e0 * out_of_band_hours / judged_hours > {{ var('gate_max_out_of_band_share') }}
