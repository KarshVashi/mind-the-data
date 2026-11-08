"""The league for a window of London dates, built from the marts.

An ISO week is read straight from fct_line_weekly, so the published weekly
table carries dbt's own ranks. A custom window (the first post runs
Wednesday to Tuesday) is summed here from fct_line_daily with the same rules:
ratios recomputed from summed minutes, rank by Disruption Index ascending,
ties to the higher Good Service %, still-equal lines sharing a rank (D20),
lines without scored minutes unranked. A test checks the two paths agree on
a full recorded week.

Nothing here changes a metric. Flags only mark numbers that rest on a
decision the owner has not made yet.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from statistics import median

from mind_the_data.publish.marts import (
    DailyRow,
    IncidentRow,
    LineInfo,
    WeeklyRow,
    rows_by_line,
)

# PLAN 7.4: a line-week under 90% completeness is provisional. Mirrors the
# dbt var `provisional_completeness`; tests read both and compare.
PROVISIONAL_COMPLETENESS = 0.9

# A line whose scheduled minutes are this much engineering works is scored on
# what is left, and under D22 a delay on top of a part closure stays in while
# the Good Service around it drops out. Such a line's index and rank depend on
# how D22 is answered, so it is marked provisional. Display only: the rank is
# not changed. The threshold itself is pending as D25.
CLOSURE_SHARE_PROVISIONAL = 0.25

# Open owner decisions that change published numbers or flags (docs/DECISIONS.md).
OPEN_METRIC_DECISIONS: tuple[str, ...] = (
    "D16",
    "D17",
    "D18",
    "D19",
    "D20",
    "D21",
    "D22",
    "D23",
    "D25",
    "D27",
    "D28",
)


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"window ends ({self.end}) before it starts ({self.start})")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def iso_week(self) -> str | None:
        """'2026-W45' when the window is exactly one ISO week, else None."""
        year, week, weekday = self.start.isocalendar()
        if weekday == 1 and self.days == 7:
            return f"{year}-W{week:02d}"
        return None

    @classmethod
    def for_iso_week(cls, iso_week: str) -> "Window":
        start = date.fromisoformat(f"{iso_week}-1")
        return cls(start=start, end=start + timedelta(days=6))


@dataclass(frozen=True)
class ProvisionalReason:
    code: str
    decisions: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class MeasuredView:
    """Experienced Reliability (PLAN 7.3). Filled once M4's marts exist."""

    excess_wait_minutes: float | None
    long_gaps_per_100_hours: float | None
    honesty_gap_hours: float | None
    honesty_gap_share: float | None
    experienced_rank: int | None


@dataclass(frozen=True)
class Standing:
    line: LineInfo
    scheduled_minutes: int
    observed_minutes: int
    engineering_works_minutes: int
    scored_minutes: int
    good_service_minutes: int
    weighted_disruption_minutes: float
    incidents: int
    median_recovery_minutes: float | None
    rank: int | None = None
    rank_movement: int | None = None
    provisional_reasons: tuple[ProvisionalReason, ...] = ()
    measured: MeasuredView | None = None

    @property
    def completeness(self) -> float | None:
        if self.scheduled_minutes == 0:
            return None
        return self.observed_minutes / self.scheduled_minutes

    @property
    def disruption_index(self) -> float | None:
        # Weighted disruption minutes per 100 scored service hours (D19).
        if self.scored_minutes == 0:
            return None
        return 6000 * self.weighted_disruption_minutes / self.scored_minutes

    @property
    def good_service_pct(self) -> float | None:
        if self.scored_minutes == 0:
            return None
        return 100 * self.good_service_minutes / self.scored_minutes

    @property
    def incidents_per_100_hours(self) -> float | None:
        if self.scored_minutes == 0:
            return None
        return 6000 * self.incidents / self.scored_minutes

    @property
    def closure_share(self) -> float | None:
        if self.scheduled_minutes == 0:
            return None
        return self.engineering_works_minutes / self.scheduled_minutes

    @property
    def is_provisional(self) -> bool:
        return bool(self.provisional_reasons)


@dataclass(frozen=True)
class WorstIncident:
    line: LineInfo
    incident: IncidentRow


@dataclass(frozen=True)
class League:
    window: Window
    standings: tuple[Standing, ...]
    worst_incident: WorstIncident | None
    # False while the data does not yet reach past the window's last London date.
    is_complete: bool = False
    latest_data_london: datetime | None = None
    open_decisions: tuple[str, ...] = field(default=OPEN_METRIC_DECISIONS)

    @property
    def completeness(self) -> float | None:
        scheduled = sum(s.scheduled_minutes for s in self.standings)
        observed = sum(s.observed_minutes for s in self.standings)
        return observed / scheduled if scheduled else None


def provisional_reasons(
    standing: Standing, *, low_completeness: bool | None = None
) -> tuple[ProvisionalReason, ...]:
    """Why a line's numbers are provisional.

    `low_completeness` passes on dbt's own is_provisional flag where the mart
    has one (fct_line_weekly); otherwise it is worked out from the minutes.
    """
    reasons: list[ProvisionalReason] = []
    completeness = standing.completeness
    if low_completeness is None:
        low_completeness = completeness is not None and completeness < PROVISIONAL_COMPLETENESS
    if completeness is None:
        reasons.append(ProvisionalReason("no_scheduled_minutes", (), "no scheduled minutes"))
    elif low_completeness:
        reasons.append(
            ProvisionalReason(
                "low_completeness",
                (),
                f"{completeness:.0%} of scheduled minutes observed",
            )
        )
    share = standing.closure_share
    if share is not None and share >= CLOSURE_SHARE_PROVISIONAL:
        reasons.append(
            ProvisionalReason(
                "mostly_engineering_works",
                ("D22", "D25"),
                f"{share:.0%} of scheduled minutes under closures; scored on the rest",
            )
        )
    return tuple(reasons)


def rank_standings(standings: Sequence[Standing]) -> list[Standing]:
    """Assign ranks (D20) and return the standings in league order.

    Equivalent to SQL `rank() over (order by disruption_index asc,
    good_service_pct desc)` over the lines with an index.
    """

    def key(s: Standing) -> tuple[float, float]:
        assert s.disruption_index is not None and s.good_service_pct is not None
        return (s.disruption_index, -s.good_service_pct)

    ranked = sorted((s for s in standings if s.disruption_index is not None), key=key)
    unranked = sorted(
        (s for s in standings if s.disruption_index is None), key=lambda s: s.line.line_name
    )
    out: list[Standing] = []
    previous_key: tuple[float, float] | None = None
    current_rank = 0
    for position, standing in enumerate(ranked, start=1):
        k = key(standing)
        if k != previous_key:
            current_rank = position
            previous_key = k
        out.append(replace(standing, rank=current_rank))
    out.extend(replace(s, rank=None) for s in unranked)
    return out


def _median_recovery(incidents: Sequence[IncidentRow]) -> float | None:
    recovered = [i.recovery_minutes for i in incidents if i.recovery_minutes is not None]
    return float(median(recovered)) if recovered else None


def worst_incident(
    lines: Sequence[LineInfo], incidents: Sequence[IncidentRow]
) -> WorstIncident | None:
    """The longest incident in the window; ties go to the higher peak weight, then the earlier."""
    by_id = {line.line_id: line for line in lines}
    candidates = [i for i in incidents if i.line_id in by_id]
    if not candidates:
        return None
    worst = min(
        candidates,
        key=lambda i: (-i.duration_minutes, -i.peak_severity_weight, i.started_at_utc),
    )
    return WorstIncident(line=by_id[worst.line_id], incident=worst)


def data_reaches_past(window: Window, latest_minute_london: datetime | None) -> bool:
    """True once the data holds a minute after the window's last London date."""
    return latest_minute_london is not None and latest_minute_london.date() > window.end


def league_from_daily(
    window: Window,
    lines: Sequence[LineInfo],
    daily: Sequence[DailyRow],
    incidents: Sequence[IncidentRow],
    latest_minute_london: datetime | None = None,
) -> League:
    """Sum fct_line_daily over a custom window and rank it (D20).

    Rank movement is left empty: there is no earlier window of the same shape
    to compare with.
    """
    in_window = [d for d in daily if window.start <= d.london_date <= window.end]
    window_incidents = [i for i in incidents if window.start <= i.start_london_date <= window.end]
    daily_by_line = rows_by_line(in_window)
    incidents_by_line = rows_by_line(window_incidents)
    standings = []
    for line in lines:
        rows = daily_by_line.get(line.line_id, [])
        standing = Standing(
            line=line,
            scheduled_minutes=sum(r.scheduled_minutes for r in rows),
            observed_minutes=sum(r.observed_minutes for r in rows),
            engineering_works_minutes=sum(r.engineering_works_minutes for r in rows),
            scored_minutes=sum(r.scored_minutes for r in rows),
            good_service_minutes=sum(r.good_service_minutes for r in rows),
            weighted_disruption_minutes=sum(r.weighted_disruption_minutes for r in rows),
            incidents=sum(r.incidents for r in rows),
            median_recovery_minutes=_median_recovery(incidents_by_line.get(line.line_id, [])),
        )
        standings.append(replace(standing, provisional_reasons=provisional_reasons(standing)))
    return League(
        window=window,
        standings=tuple(rank_standings(standings)),
        worst_incident=worst_incident(lines, window_incidents),
        is_complete=data_reaches_past(window, latest_minute_london),
        latest_data_london=latest_minute_london,
    )


def league_from_weekly(
    week_start: date,
    lines: Sequence[LineInfo],
    weekly: Sequence[WeeklyRow],
    incidents: Sequence[IncidentRow],
    latest_minute_london: datetime | None = None,
) -> League:
    """The ISO week as fct_line_weekly ranked and flagged it."""
    window = Window(start=week_start, end=week_start + timedelta(days=6))
    rows = {w.line_id: w for w in weekly if w.week_start == week_start}
    standings = []
    for line in lines:
        row = rows.get(line.line_id)
        if row is None:
            standing = Standing(line, 0, 0, 0, 0, 0, 0.0, 0, None)
        else:
            standing = Standing(
                line=line,
                scheduled_minutes=row.scheduled_minutes,
                observed_minutes=row.observed_minutes,
                engineering_works_minutes=row.engineering_works_minutes,
                scored_minutes=row.scored_minutes,
                good_service_minutes=row.good_service_minutes,
                weighted_disruption_minutes=row.weighted_disruption_minutes,
                incidents=row.incidents,
                median_recovery_minutes=row.median_recovery_minutes,
                rank=row.disruption_rank,
                rank_movement=row.rank_movement,
            )
        flagged = provisional_reasons(
            standing, low_completeness=None if row is None else row.is_provisional
        )
        standings.append(replace(standing, provisional_reasons=flagged))
    standings.sort(
        key=lambda s: (s.rank is None, s.rank if s.rank is not None else 0, s.line.line_name)
    )
    week_incidents = [i for i in incidents if window.start <= i.start_london_date <= window.end]
    week_over = bool(rows) and all(r.is_week_complete for r in rows.values())
    return League(
        window=window,
        standings=tuple(standings),
        worst_incident=worst_incident(lines, week_incidents),
        is_complete=week_over and data_reaches_past(window, latest_minute_london),
        latest_data_london=latest_minute_london,
    )
