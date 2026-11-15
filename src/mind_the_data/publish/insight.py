"""The one-sentence insight on a share card.

The plan fills it from the week's biggest Honesty Gap (PLAN 7.3). With the
measured view published (D42), the insight is, in order (D38, D54):

1. the most Honesty Gap hours on a line, only while
   `HONESTY_GAP_HEADLINE` is on. It is off by default (D54): until
   ADR-014's open item 2 is resolved, boards that leave trains out can
   create long gaps that did not happen, so neither Honesty Gap hours nor
   long-gap counts head the card. The Honesty Gap stays in the files, with
   its caveat;
2. otherwise the lowest trains-run share, when it is under
   `TRAINS_RUN_HEADLINE_BELOW`;
3. otherwise a declared-view fact: the lowest Good Service % among ranked
   lines. Good Service % is over scored minutes, which leave out closures,
   Service Closed and missing minutes, hence "scored service time".

A line is named only if its numbers are not provisional, and for the
measured headlines only if they are not weak evidence (D33, D53): an
estimated timetable is not a basis for a claim on the card.
"""

from dataclasses import dataclass

from mind_the_data.publish.league import League, Standing

# D54 (open): the Honesty Gap heads the card only once ADR-014's open item 2
# (boards omitting trains inflate long gaps) is resolved. Flip to True then.
HONESTY_GAP_HEADLINE = False

# Trains run near the timetable is not news; a share under this is.
TRAINS_RUN_HEADLINE_BELOW = 0.95


@dataclass(frozen=True)
class Insight:
    kind: str  # "honesty_gap", "trains_run" or "lowest_good_service"
    line_id: str
    text: str
    # A qualifier the card prints under the text, when the claim alone can be misread.
    caveat: str | None = None


# 67% of trains showing on the boards reads as "33% cancelled", but the boards
# can omit trains that did run (ADR-014, open item 2), so the card says so.
TRAINS_RUN_CAVEAT = "Boards can miss trains that ran."


def _measured_candidates(league: League) -> list[Standing]:
    return [
        s
        for s in league.standings
        if s.measured is not None and not s.measured.is_weak_evidence and not s.is_provisional
    ]


def pick_insight(
    league: League, *, honesty_gap_headline: bool = HONESTY_GAP_HEADLINE
) -> Insight | None:
    measured = _measured_candidates(league)
    gaps = (
        []
        if not honesty_gap_headline
        else [
            (s.measured.honesty_gap_hours, s)
            for s in measured
            if s.measured is not None
            and s.measured.honesty_gap_hours is not None
            and s.measured.honesty_gap_hours > 0
        ]
    )
    if gaps:
        hours, standing = max(gaps, key=lambda g: (g[0], g[1].line.line_name))
        unit = "hour" if round(hours) == 1 else "hours"
        return Insight(
            kind="honesty_gap",
            line_id=standing.line.line_id,
            text=(
                f"{line_label(standing.line.line_name)} showed Good Service for "
                f"{hours:.0f} {unit} in which its trains ran with long gaps."
            ),
        )

    shares = [
        (s.measured.trains_run_share, s)
        for s in measured
        if s.measured is not None
        and s.measured.trains_run_share is not None
        and s.measured.trains_run_share < TRAINS_RUN_HEADLINE_BELOW
    ]
    if shares:
        share, standing = min(shares, key=lambda g: (g[0], g[1].line.line_name))
        return Insight(
            kind="trains_run",
            line_id=standing.line.line_id,
            text=(
                f"Only {share:.0%} of {possessive(standing.line.line_name)} timetabled trains "
                "showed on TfL's arrival boards."
            ),
            caveat=TRAINS_RUN_CAVEAT,
        )

    return lowest_good_service_insight(league)


def lowest_good_service_insight(league: League) -> Insight | None:
    """The declared-view fact: the lowest Good Service % among ranked lines."""
    declared = [
        s
        for s in league.standings
        if s.good_service_pct is not None and not s.is_provisional and s.rank is not None
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


def possessive(name: str) -> str:
    """'the Central line's', 'the DLR's', 'the trams''."""
    label = line_label(name)
    label = label[0].lower() + label[1:]
    return f"{label}'" if label.endswith("s") else f"{label}'s"
