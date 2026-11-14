"""Sensitivity of the weekly ranks to the weights (PLAN 7.5, DECISIONS.md D61).

Each week's ranked lines are re-ranked under the alternative weight sets in
seeds/sensitivity_weight_sets.csv, re-weighting rpt_weekly_status_mix (the
unweighted scored minutes per status and time band). Reported per week and
set: Spearman's rho against the published ranks, the largest rank shift,
and whether the top three or bottom three lines change.

The ranking rule is the league's (D20): index ascending, ties to the higher
Good Service %, which no weight changes. Only lines ranked in the published
week take part, since D22 does not depend on the weights. A set re-weights
each minute's worst status as ADR-004 chose it under the base weights.
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from math import sqrt
from pathlib import Path
from typing import Any

from mind_the_data.publish.marts import StatusMixRow

TOP_BOTTOM = 3


@dataclass(frozen=True)
class WeightSet:
    name: str
    description: str
    # severity code -> (weight unplanned, weight planned)
    severity: dict[int, tuple[float, float]] = field(default_factory=dict)
    time_band: dict[str, float] = field(default_factory=dict)

    def severity_weight(self, code: int, is_planned: bool) -> float:
        unplanned, planned = self.severity[code]
        return planned if is_planned else unplanned


def load_weight_sets(seeds_dir: Path) -> tuple[WeightSet, tuple[WeightSet, ...]]:
    """The base weights from the metric seeds, and each alternative set over them."""
    with (seeds_dir / "severity_weights.csv").open(newline="") as f:
        severity = {
            int(r["severity_code"]): (float(r["weight_unplanned"]), float(r["weight_planned"]))
            for r in csv.DictReader(f)
        }
    with (seeds_dir / "time_bands.csv").open(newline="") as f:
        bands = {r["time_band"]: float(r["time_weight"]) for r in csv.DictReader(f)}
    base = WeightSet("base", "The published weights (PLAN 7.1)", severity, bands)

    with (seeds_dir / "sensitivity_weight_sets.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    alternatives = []
    for name in dict.fromkeys(r["weight_set"] for r in rows):
        own = [r for r in rows if r["weight_set"] == name]
        alt_severity = dict(severity)
        alt_bands = dict(bands)
        for r in own:
            weight = float(r["weight"])
            if r["factor"] == "severity":
                code = int(r["factor_key"])
                if code not in severity:
                    raise ValueError(f"{name}: unknown severity code {code}")
                # A status weighted 0 when planned (Bus Service) stays 0.
                planned = weight if severity[code][1] > 0 else 0.0
                alt_severity[code] = (weight, planned)
            elif r["factor"] == "time_band":
                if r["factor_key"] not in bands:
                    raise ValueError(f"{name}: unknown time band {r['factor_key']}")
                alt_bands[r["factor_key"]] = weight
            else:
                raise ValueError(f"{name}: unknown factor {r['factor']}")
        alternatives.append(WeightSet(name, own[0]["description"], alt_severity, alt_bands))
    return base, tuple(alternatives)


def indexes_under(rows: Sequence[StatusMixRow], weights: WeightSet) -> dict[str, float]:
    """Disruption Index per line under a weight set (D19's formula)."""
    numerator: dict[str, float] = {}
    denominator: dict[str, float] = {}
    for r in rows:
        time_weight = weights.time_band[r.time_band]
        severity = weights.severity_weight(r.status_severity, r.is_planned)
        numerator[r.line_id] = (
            numerator.get(r.line_id, 0.0) + r.scored_minutes * severity * time_weight
        )
        denominator[r.line_id] = denominator.get(r.line_id, 0.0) + r.scored_minutes * time_weight
    return {line: 100 * numerator[line] / d for line, d in denominator.items() if d > 0}


def competition_ranks(keys: Sequence[tuple[float, float]]) -> list[int]:
    """SQL rank(): equal keys share the lowest position."""
    ordered = sorted(keys)
    return [ordered.index(k) + 1 for k in keys]


def average_ranks(keys: Sequence[tuple[float, float]]) -> list[float]:
    """Ranks with ties given the mean of the positions they span (for Spearman)."""
    ordered = sorted(keys)
    out = []
    for k in keys:
        first = ordered.index(k) + 1
        count = ordered.count(k)
        out.append(first + (count - 1) / 2)
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson correlation of two rank vectors; None when either has no spread."""
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    return sxy / sqrt(sxx * syy)


@dataclass(frozen=True)
class RankedLine:
    line_id: str
    disruption_index: float
    good_service_pct: float


@dataclass(frozen=True)
class SetResult:
    weight_set: str
    spearman_rho: float | None
    max_rank_shift: int
    top_three_changed: bool
    bottom_three_changed: bool


def _ends(order: Sequence[str]) -> tuple[set[str], set[str]]:
    return set(order[:TOP_BOTTOM]), set(order[-TOP_BOTTOM:])


def week_sensitivity(
    ranked: Sequence[RankedLine], mix: Sequence[StatusMixRow], sets: Sequence[WeightSet]
) -> list[SetResult]:
    """Compare the published ranks with each set's ranks for one week.

    Returns nothing when a ranked line has no status mix rows (the mart is
    behind the league): a missing line must never be re-scored as perfect.
    """
    lines = sorted(ranked, key=lambda r: r.line_id)
    in_mix = {m.line_id for m in mix if m.scored_minutes > 0}
    if any(r.line_id not in in_mix for r in lines):
        return []
    base_keys = [(r.disruption_index, -r.good_service_pct) for r in lines]
    base_order = [
        r.line_id
        for _, r in sorted(zip(base_keys, lines, strict=True), key=lambda p: (p[0], p[1].line_id))
    ]
    base_ranks = competition_ranks(base_keys)
    results = []
    for weights in sets:
        alt = indexes_under([m for m in mix if m.line_id in {r.line_id for r in lines}], weights)
        alt_keys = [(alt[r.line_id], -r.good_service_pct) for r in lines]
        alt_order = [
            r.line_id
            for _, r in sorted(
                zip(alt_keys, lines, strict=True), key=lambda p: (p[0], p[1].line_id)
            )
        ]
        alt_ranks = competition_ranks(alt_keys)
        base_top, base_bottom = _ends(base_order)
        alt_top, alt_bottom = _ends(alt_order)
        results.append(
            SetResult(
                weight_set=weights.name,
                spearman_rho=spearman(average_ranks(base_keys), average_ranks(alt_keys)),
                max_rank_shift=max(
                    (abs(a - b) for a, b in zip(base_ranks, alt_ranks, strict=True)), default=0
                ),
                top_three_changed=base_top != alt_top,
                bottom_three_changed=base_bottom != alt_bottom,
            )
        )
    return results


def sensitivity_document(
    weeks: Sequence[tuple[date, str, Sequence[RankedLine]]],
    mix: Sequence[StatusMixRow],
    sets: Sequence[WeightSet],
) -> dict[str, Any]:
    """The `sensitivity` block of meta/methodology.json.

    `weeks` holds (week_start, iso_week, ranked lines) for each published week.
    A week with no status mix rows (not yet built) is reported without results.
    """
    out_weeks = []
    for week_start, iso_week, ranked in sorted(weeks, key=lambda w: w[0]):
        week_mix = [m for m in mix if m.week_start == week_start]
        results = week_sensitivity(ranked, week_mix, sets) if week_mix and ranked else []
        out_weeks.append(
            {
                "iso_week": iso_week,
                "lines_ranked": len(ranked),
                "results": [
                    {
                        "weight_set": r.weight_set,
                        "spearman_rho": None
                        if r.spearman_rho is None
                        else round(r.spearman_rho, 4),
                        "max_rank_shift": r.max_rank_shift,
                        "top_three_changed": r.top_three_changed,
                        "bottom_three_changed": r.bottom_three_changed,
                    }
                    for r in results
                ],
            }
        )
    return {
        "method": (
            "Each published week's ranked lines re-ranked under each weight set from the "
            "same minutes; Spearman's rho against the published ranks (ties averaged), "
            "the largest change of rank, and whether the top or bottom three change "
            "(PLAN 7.5, D61)."
        ),
        "weight_sets": [
            {
                "weight_set": s.name,
                "description": s.description,
                "severity_weights": [
                    {"severity_code": code, "weight_unplanned": w[0], "weight_planned": w[1]}
                    for code, w in sorted(s.severity.items())
                ],
                "time_weights": [
                    {"time_band": band, "time_weight": w} for band, w in sorted(s.time_band.items())
                ],
            }
            for s in sets
        ],
        "weeks": out_weeks,
    }
