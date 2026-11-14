-- PLAN 7.4: the "strikes excluded" league must be the main league in any
-- week with no industrial action on any line. Returns the line-weeks where
-- the two disagree.

select
    variant.line_id,
    variant.week_start,
    variant.disruption_index as variant_index,
    league.disruption_index as league_index,
    variant.disruption_rank as variant_rank,
    league.disruption_rank as league_rank
from {{ ref('rpt_league_strikes_excluded') }} as variant
full outer join {{ ref('fct_line_weekly') }} as league
    on
        variant.line_id = league.line_id
        and variant.week_start = league.week_start
where
    not coalesce(variant.week_has_industrial_action, false)
    and (
        variant.line_id is null
        or league.line_id is null
        or variant.disruption_rank is distinct from league.disruption_rank
        or variant.unranked_reason is distinct from league.unranked_reason
        or variant.scored_minutes != league.scored_minutes
        or abs(coalesce(variant.disruption_index, -1) - coalesce(league.disruption_index, -1))
        > 1e-9
    )
