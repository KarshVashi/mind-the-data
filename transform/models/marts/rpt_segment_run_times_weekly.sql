{{ config(materialized='table', tags=['exploratory']) }}

-- Run times between adjacent stations per London week (D72), from
-- int_segment_run_times: for each line, direction and segment, the number
-- of runs and the 10th, 50th and 90th percentile of the run in seconds.
-- Variability is p90 minus p50: how much longer a bad run takes than a
-- typical one. Percentiles are nearest-rank on the exact values, so they
-- are always a run that happened.
--
-- A segment is reported only with `segment_min_runs` runs in the week.
-- `paired_share` is the runs over the vehicle-id passes at the segment's
-- first station: the share of trains we could follow to the next one, and
-- the coverage figure for lines whose ids are only partly reliable
-- (`is_partial_id_line`). At a junction the passes split across the
-- outgoing segments, so the share reads low there by design.
-- Times include one dwell and carry the boards' resolution.
-- Never published; nothing here is read as a good time.

with runs as (
    select
        line_id,
        direction,
        from_naptan_id,
        to_naptan_id,
        london_date,
        cast(date_trunc('week', london_date) as date) as week_start,
        run_seconds
    from {{ ref('int_segment_run_times') }}
),

segments as (
    select
        line_id,
        direction,
        from_naptan_id,
        to_naptan_id,
        week_start,
        count(*) as runs,
        count(distinct london_date) as days_with_runs,
        array_sort(array_agg(run_seconds)) as sorted_seconds
    from runs
    group by line_id, direction, from_naptan_id, to_naptan_id, week_start
),

pass_weeks as (
    select
        line_id,
        direction,
        naptan_id,
        cast(date_trunc('week', cast({{ london_local('pass_time') }} as date)) as date)
            as week_start
    from {{ ref('int_station_vehicle_passes') }}
),

from_passes as (
    select
        line_id,
        direction,
        naptan_id,
        week_start,
        count(*) as passes
    from pass_weeks
    group by line_id, direction, naptan_id, week_start
)

select
    {{ row_key([
        'segments.line_id',
        'segments.direction',
        'segments.from_naptan_id',
        'segments.to_naptan_id',
        'segments.week_start',
    ]) }}
        as segment_week_key,
    segments.week_start,
    cast(year_of_week(segments.week_start) as varchar)
    || '-W'
    || lpad(cast(week_of_year(segments.week_start) as varchar), 2, '0') as iso_week,
    segments.line_id,
    segments.direction,
    segments.from_naptan_id,
    segments.to_naptan_id,
    from_station.station_name as from_station_name,
    to_station.station_name as to_station_name,
    segments.runs,
    segments.days_with_runs,
    element_at(segments.sorted_seconds, cast(ceil(0.1e0 * segments.runs) as integer))
        as p10_seconds,
    element_at(segments.sorted_seconds, cast(ceil(0.5e0 * segments.runs) as integer))
        as p50_seconds,
    element_at(segments.sorted_seconds, cast(ceil(0.9e0 * segments.runs) as integer))
        as p90_seconds,
    element_at(segments.sorted_seconds, cast(ceil(0.9e0 * segments.runs) as integer))
    - element_at(segments.sorted_seconds, cast(ceil(0.5e0 * segments.runs) as integer))
        as variability_seconds,
    1e0 * segments.runs / from_passes.passes as paired_share,
    contains(
        array[
            {%- for partial_line in var('segment_partial_id_lines') %}
                '{{ partial_line }}'{{ ',' if not loop.last }}
            {%- endfor %}
        ],
        segments.line_id
    ) as is_partial_id_line
from segments
left join from_passes
    on
        segments.line_id = from_passes.line_id
        and segments.direction = from_passes.direction
        and segments.from_naptan_id = from_passes.naptan_id
        and segments.week_start = from_passes.week_start
left join {{ ref('dim_station') }} as from_station
    on segments.from_naptan_id = from_station.naptan_id
left join {{ ref('dim_station') }} as to_station
    on segments.to_naptan_id = to_station.naptan_id
where segments.runs >= {{ var('segment_min_runs') }}
