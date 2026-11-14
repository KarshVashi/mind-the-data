-- D19 (decided 2026-11-14): the Disruption Index is the time-weighted mean
-- severity x 100, so it lies between 0 (Good Service throughout) and 100 x
-- the largest severity weight (800, Suspended throughout). A value outside
-- that range means the numerator and denominator have drifted apart.

with weights as (
    select max(greatest(weight_unplanned, weight_planned)) as max_weight
    from {{ ref('severity_weights') }}
),

indexes as (
    select
        'daily' as grain,
        line_id,
        london_date as period_start,
        disruption_index
    from {{ ref('fct_line_daily') }}
    union all
    select
        'weekly' as grain,
        line_id,
        week_start as period_start,
        disruption_index
    from {{ ref('fct_line_weekly') }}
)

select
    indexes.grain,
    indexes.line_id,
    indexes.period_start,
    indexes.disruption_index
from indexes
cross join weights
where
    indexes.disruption_index < 0
    or indexes.disruption_index > 100 * weights.max_weight + 1e-9
