"""The one-sentence insight on a share card.

The plan fills it from the week's biggest Honesty Gap (PLAN 7.3). Until the
measured view exists, or when no line has a gap, it falls back to a
declared-view fact: the lowest Good Service % among lines whose numbers are
not provisional (D28). Good Service % is over scored minutes, which leave out
closures, Service Closed and missing minutes, hence "scored service time".
"""

from dataclasses import dataclass

from mind_the_data.publish.league import League


@dataclass(frozen=True)
class Insight:
    kind: str  # "honesty_gap" or "lowest_good_service"
    line_id: str
    text: str


def pick_insight(league: League) -> Insight | None:
    gaps = [
        (s.measured.honesty_gap_hours, s)
        for s in league.standings
        if s.measured is not None
        and s.measured.honesty_gap_hours is not None
        and s.measured.honesty_gap_hours > 0
    ]
    if gaps:
        hours, standing = max(gaps, key=lambda g: (g[0], g[1].line.line_name))
        return Insight(
            kind="honesty_gap",
            line_id=standing.line.line_id,
            text=(
                f"{line_label(standing.line.line_name)} showed Good Service for {hours:.0f} hours "
                "while trains ran with long gaps."
            ),
        )

    declared = [
        s for s in league.standings if s.good_service_pct is not None and not s.is_provisional
    ]
    if not declared:
        return None
    worst = min(declared, key=lambda s: (s.good_service_pct or 0.0, s.line.line_name))
    pct = worst.good_service_pct or 0.0
    return Insight(
        kind="lowest_good_service",
        line_id=worst.line.line_id,
        text=(
            f"{line_label(worst.line.line_name)} showed Good Service for only {pct:.0f}% "
            "of its scored service time."
        ),
    )


def line_label(name: str) -> str:
    """'The Central line', 'The Elizabeth line', 'The DLR', 'Trams'."""
    if name == "Tram":
        return "Trams"
    if name.lower().endswith(" line") or name.isupper():
        return f"The {name}"
    return f"The {name} line"
