"""The league for a window of London dates, built from the marts.

An ISO week is read straight from fct_line_weekly, so the published weekly
table carries dbt's own ranks. A custom window (the first post runs
Wednesday to Tuesday) is summed here from fct_line_daily with the same rules:
ratios recomputed from summed minutes, the Disruption Index as the
time-weighted mean severity x 100 (D19), rank by it ascending, ties to the
higher Good Service %, still-equal lines sharing a rank (D20), lines without
scored minutes or that ran under half their scheduled minutes because of
engineering works unranked (D22). A test checks the two paths agree on a
full recorded week.

The week's highlights (M6) are worked out here for both paths, mirroring
rpt_weekly_highlights: the Wooden Spoon, Line of the Week and the single
worst incident.

The measured view (PLAN 7.3, published since D42 was settled) follows the
same pattern: an ISO week takes fct_line_weekly's sums, withheld reason and
experienced rank; a custom window sums fct_line_measured_daily and applies
the same rules here (D51 to D53).

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
    MeasuredDailyRow,
    MeasuredSums,
    MondayMorningRow,
    WeeklyRow,
    rows_by_line,
)

# PLAN 7.4: a line-week under 90% completeness is provisional. Mirrors the
# dbt var `provisional_completeness`; tests read both and compare.
PROVISIONAL_COMPLETENESS = 0.9

# A line whose scheduled minutes are this much engineering works is scored on
# what is left, and a delay on top of a part closure stays in while the Good
# Service around it drops out, so its index leans on few hours. It is marked
# provisional; the rank is not changed. The threshold is pending as D35.
CLOSURE_SHARE_PROVISIONAL = 0.25

# D22 (decided 2026-11-14): a line is ranked only if it ran at least this
# share of its scheduled minutes, engineering works taking the rest.
# Mirrors the dbt var `min_open_share_to_rank`; a test compares them.
MIN_OPEN_SHARE_TO_RANK = 0.5

UNRANKED_NO_SCORED_MINUTES = "no_scored_minutes"
UNRANKED_ENGINEERING_WORKS = "engineering_works"

# Measured view. Each mirrors a dbt var; a test reads both and compares.
# A line-week's gate fails when more than this share of its judged hours at
# core and branch stations is out of band (D51; the M4 test's limit).
GATE_MAX_OUT_OF_BAND_SHARE = 0.3
# Below this share of scheduled station-direction-hours measured, the line's
# measured numbers are withheld (D42, `min_measured_share`).
MIN_MEASURED_SHARE = 0.5
# Lines whose boards misfile trains on the shared section (ADR-014): the
# whole measured view is withheld (D52), and trains run with it.
MEASURED_VIEW_WITHHELD_LINES: tuple[str, ...] = ("circle", "hammersmith-city")
TRAINS_RUN_WITHHELD_LINES: tuple[str, ...] = ("circle", "hammersmith-city")
WITHHELD_SHARED_SECTION = "shared_section_misfiled"

# Open owner decisions that change published numbers or flags (docs/DECISIONS.md).
OPEN_METRIC_DECISIONS: tuple[str, ...] = (
    "D16",
    "D17",
    "D18",
    "D20",
    "D21",
    "D23",
    # The measured view's definitions, published since D42 was settled.
    "D25",
    "D26",
    "D27",
    "D28",
    "D29",
    "D30",
    "D31",
    "D32",
    "D33",
    "D34",
    "D35",
    "D37",
    "D38",
    "D40",
    "D43",
    "D51",
    "D52",
    "D53",
)

# Owner decisions settled since publishing began, listed in meta/methodology.json.
SETTLED_METRIC_DECISIONS: tuple[str, ...] = (
    "D19",
    "D22",
    "D36",
    "D42",
    "D44",
    "D46",
    "D47",
    "D48",
    "D49",
    "D50",
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
    """Experienced Reliability (PLAN 7.3) for a line whose measured numbers are published (D42)."""

    excess_wait_minutes: float | None
    long_gaps_per_100_hours: float | None
    honesty_gap_hours: float | None
    honesty_gap_share: float | None
    experienced_rank: int | None
    # Trains on the boards over scheduled trains (ADR-014); the boards may omit trains.
    trains_run_share: float | None = None
    measured_hours: int = 0
    measured_share: float | None = None
    gate_pass_share: float | None = None
    good_service_measured_hours: int = 0
    # Judged against the fallback frequencies of D33: weak evidence, unranked (D53).
    is_weak_evidence: bool = False
    trains_run_withheld_reason: str | None = None


def measured_withheld_reason(line: LineInfo, sums: MeasuredSums | None) -> str | None:
    """Why a line-week's measured numbers are not published; None when they are.

    The first that applies, as fct_line_weekly.measured_withheld_reason.
    """
    if not line.is_measured:
        return "line_not_measured"
    if line.line_id in MEASURED_VIEW_WITHHELD_LINES:
        return WITHHELD_SHARED_SECTION
    if sums is None or sums.measured_hours == 0:
        return "no_measured_hours"
    if sums.gate_judged_hours == 0:
        return "gate_not_judged"
    if sums.gate_in_band_hours < (1 - GATE_MAX_OUT_OF_BAND_SHARE) * sums.gate_judged_hours:
        return "gate_failed"
    if sums.measured_hours < MIN_MEASURED_SHARE * sums.scheduled_measurement_hours:
        return "too_few_measured_hours"
    return None


def measured_view(line: LineInfo, sums: MeasuredSums, experienced_rank: int | None) -> MeasuredView:
    """The published measured numbers from a window's sums, as fct_line_weekly computes them."""
    trains_run_withheld = (
        WITHHELD_SHARED_SECTION if line.line_id in TRAINS_RUN_WITHHELD_LINES else None
    )
    return MeasuredView(
        excess_wait_minutes=sums.sum_excess_wait_seconds / sums.excess_wait_hours / 60
        if sums.excess_wait_hours
        else None,
        long_gaps_per_100_hours=100 * sums.long_gaps / sums.measured_hours
        if sums.measured_hours
        else None,
        honesty_gap_hours=float(sums.honesty_gap_hours),
        honesty_gap_share=sums.honesty_gap_hours / sums.good_service_measured_hours
        if sums.good_service_measured_hours
        else None,
        experienced_rank=experienced_rank,
        trains_run_share=sums.observed_trains / sums.observed_hours_scheduled_trains
        if sums.observed_hours_scheduled_trains and trains_run_withheld is None
        else None,
        measured_hours=sums.measured_hours,
        measured_share=sums.measured_hours / sums.scheduled_measurement_hours
        if sums.scheduled_measurement_hours
        else None,
        gate_pass_share=sums.gate_in_band_hours / sums.gate_judged_hours
        if sums.gate_judged_hours
        else None,
        good_service_measured_hours=sums.good_service_measured_hours,
        is_weak_evidence=sums.fallback_measured_hours > 0,
        trains_run_withheld_reason=trains_run_withheld,
    )


def rank_measured(standings: Sequence["Standing"]) -> list["Standing"]:
    """Experienced Reliability rank (PLAN 7.3) over published, strong-evidence lines.

    Lowest excess wait first, ties to fewer long gaps per 100 hours, equal
    lines sharing a rank, as SQL rank() in fct_line_weekly (D51, D53).
    """

    def key(s: Standing) -> tuple[float, float]:
        assert s.measured is not None and s.measured.excess_wait_minutes is not None
        # excess_wait_hours > 0 implies measured_hours > 0, so long gaps is never null here.
        assert s.measured.long_gaps_per_100_hours is not None
        return (s.measured.excess_wait_minutes, s.measured.long_gaps_per_100_hours)

    rankable = sorted(
        (
            s
            for s in standings
            if s.measured is not None
            and s.measured.excess_wait_minutes is not None
            and not s.measured.is_weak_evidence
        ),
        key=key,
    )
    ranks: dict[str, int] = {}
    previous: tuple[float, float] | None = None
    current = 0
    for position, standing in enumerate(rankable, start=1):
        if key(standing) != previous:
            current, previous = position, key(standing)
        ranks[standing.line.line_id] = current
    out = []
    for s in standings:
        if s.measured is None:
            out.append(s)
        else:
            measured = replace(s.measured, experienced_rank=ranks.get(s.line.line_id))
            out.append(replace(s, measured=measured))
    return out


@dataclass(frozen=True)
class Standing:
    line: LineInfo
    scheduled_minutes: int
    observed_minutes: int
    engineering_works_minutes: int
    scored_minutes: int
    good_service_minutes: int
    weighted_disruption_minutes: float
    time_weighted_scored_minutes: float
    incidents: int
    median_recovery_minutes: float | None
    rank: int | None = None
    rank_movement: int | None = None
    provisional_reasons: tuple[ProvisionalReason, ...] = ()
    measured: MeasuredView | None = None
    # Why measured is None (D42, D43, D51, D52); "line_not_measured" by default.
    measured_withheld_reason: str | None = "line_not_measured"

    @property
    def completeness(self) -> float | None:
        if self.scheduled_minutes == 0:
            return None
        return self.observed_minutes / self.scheduled_minutes

    @property
    def disruption_index(self) -> float | None:
        # 100 x the time-weighted mean severity, 0 to 800 (D19).
        if self.time_weighted_scored_minutes <= 0:
            return None
        return 100 * self.weighted_disruption_minutes / self.time_weighted_scored_minutes

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
    def engineering_works_hours(self) -> float:
        return self.engineering_works_minutes / 60

    @property
    def unranked_reason(self) -> str | None:
        """Why the line gets no rank (D20, D22); None when it can be ranked."""
        # Works first: a line closed all week has no scored minutes either.
        share = self.closure_share
        if share is not None and share > 1 - MIN_OPEN_SHARE_TO_RANK:
            return UNRANKED_ENGINEERING_WORKS
        if self.disruption_index is None:
            return UNRANKED_NO_SCORED_MINUTES
        return None

    @property
    def is_provisional(self) -> bool:
        return bool(self.provisional_reasons)


@dataclass(frozen=True)
class WorstIncident:
    line: LineInfo
    incident: IncidentRow


@dataclass(frozen=True)
class WoodenSpoon:
    """The bottom-ranked line (D48)."""

    standing: Standing
    shared_by: int


@dataclass(frozen=True)
class LineOfTheWeek:
    """The most improved line since the previous week (D47), or why there is none."""

    line: LineInfo | None
    disruption_index: float | None = None
    previous_disruption_index: float | None = None
    null_reason: str | None = None

    @property
    def improvement(self) -> float | None:
        if self.disruption_index is None or self.previous_disruption_index is None:
            return None
        return self.previous_disruption_index - self.disruption_index


@dataclass(frozen=True)
class MondayMorning:
    """A line's Monday Morning Index for one Monday (D49)."""

    line: LineInfo
    row: MondayMorningRow


@dataclass(frozen=True)
class League:
    window: Window
    standings: tuple[Standing, ...]
    worst_incident: WorstIncident | None
    # False while the data does not yet reach past the window's last London date.
    is_complete: bool = False
    latest_data_london: datetime | None = None
    open_decisions: tuple[str, ...] = field(default=OPEN_METRIC_DECISIONS)
    line_of_the_week: LineOfTheWeek = field(
        default_factory=lambda: LineOfTheWeek(None, null_reason="custom_window")
    )
    monday_morning: tuple[MondayMorning, ...] = ()

    @property
    def wooden_spoon(self) -> WoodenSpoon | None:
        return wooden_spoon(self.standings)

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
                ("D35",),
                f"{share:.0%} of scheduled minutes under closures; scored on the rest",
            )
        )
    return tuple(reasons)


def rank_standings(standings: Sequence[Standing]) -> list[Standing]:
    """Assign ranks (D20, D22) and return the standings in league order.

    Equivalent to SQL `rank() over (order by disruption_index asc,
    good_service_pct desc)` over the lines that can be ranked. Unranked lines
    follow by name.
    """

    def key(s: Standing) -> tuple[float, float]:
        assert s.disruption_index is not None and s.good_service_pct is not None
        return (s.disruption_index, -s.good_service_pct)

    ranked = sorted((s for s in standings if s.unranked_reason is None), key=key)
    unranked = sorted(
        (s for s in standings if s.unranked_reason is not None), key=lambda s: s.line.line_name
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
    """The incident with the most severity-weighted minutes (D48).

    Ties go to the longer, then the earlier, then the line id, as in
    rpt_weekly_highlights.
    """
    by_id = {line.line_id: line for line in lines}
    candidates = [i for i in incidents if i.line_id in by_id]
    if not candidates:
        return None
    worst = min(
        candidates,
        key=lambda i: (
            -i.severity_weighted_minutes,
            -i.duration_minutes,
            i.started_at_utc,
            i.line_id,
        ),
    )
    return WorstIncident(line=by_id[worst.line_id], incident=worst)


def wooden_spoon(standings: Sequence[Standing]) -> WoodenSpoon | None:
    """The bottom-ranked line (D48); lines sharing the bottom rank are split by line id."""
    ranked = [s for s in standings if s.rank is not None]
    if not ranked:
        return None
    bottom = max(s.rank for s in ranked if s.rank is not None)
    tied = sorted((s for s in ranked if s.rank == bottom), key=lambda s: s.line.line_id)
    return WoodenSpoon(standing=tied[0], shared_by=len(tied))


def week_is_complete(rows: Sequence[WeeklyRow]) -> bool:
    """A week has ended and enough of it was observed to compare with (D47)."""
    scheduled = sum(r.scheduled_minutes for r in rows)
    observed = sum(r.observed_minutes for r in rows)
    return (
        bool(rows)
        and all(r.is_week_complete for r in rows)
        and scheduled > 0
        and observed / scheduled >= PROVISIONAL_COMPLETENESS
    )


def line_of_the_week(
    week_start: date, lines: Sequence[LineInfo], weekly: Sequence[WeeklyRow]
) -> LineOfTheWeek:
    """The largest fall in Disruption Index since the previous week (D47).

    Only lines ranked and not provisional in both weeks qualify; ties go to
    the lower index this week, then the line id. Mirrors
    rpt_weekly_highlights.
    """
    previous_start = week_start - timedelta(days=7)
    this_week = {w.line_id: w for w in weekly if w.week_start == week_start}
    previous = [w for w in weekly if w.week_start == previous_start]
    if not previous or sum(w.observed_minutes for w in previous) == 0:
        return LineOfTheWeek(None, null_reason="no_previous_week")
    if not week_is_complete(previous):
        return LineOfTheWeek(None, null_reason="previous_week_incomplete")
    by_id = {line.line_id: line for line in lines}
    candidates = []
    for before in previous:
        now = this_week.get(before.line_id)
        if (
            now is None
            or before.line_id not in by_id
            or now.disruption_rank is None
            or before.disruption_rank is None
            or now.is_provisional
            or before.is_provisional
            or now.disruption_index is None
            or before.disruption_index is None
            or before.disruption_index <= now.disruption_index
        ):
            continue
        candidates.append((now, before))
    if not candidates:
        return LineOfTheWeek(None, null_reason="no_line_improved")
    now, before = min(
        candidates,
        key=lambda pair: (
            -((pair[1].disruption_index or 0.0) - (pair[0].disruption_index or 0.0)),
            pair[0].disruption_index or 0.0,
            pair[0].line_id,
        ),
    )
    return LineOfTheWeek(
        line=by_id[now.line_id],
        disruption_index=now.disruption_index,
        previous_disruption_index=before.disruption_index,
    )


def monday_mornings(
    window: Window, lines: Sequence[LineInfo], rows: Sequence[MondayMorningRow]
) -> tuple[MondayMorning, ...]:
    """The Monday Morning Index rows for the Mondays in the window, in rank order.

    A Monday with nothing observed on any line (before the collector began)
    is left out rather than published as a column of blanks.
    """
    by_id = {line.line_id: line for line in lines}
    in_window = [
        r for r in rows if window.start <= r.week_start <= window.end and r.line_id in by_id
    ]
    observed = {r.week_start for r in in_window if r.completeness > 0}
    picked = [
        MondayMorning(line=by_id[r.line_id], row=r) for r in in_window if r.week_start in observed
    ]
    picked.sort(
        key=lambda m: (
            m.row.week_start,
            m.row.monday_morning_rank is None,
            m.row.monday_morning_rank or 0,
            m.line.line_name,
        )
    )
    return tuple(picked)


def data_reaches_past(window: Window, latest_minute_london: datetime | None) -> bool:
    """True once the data holds a minute after the window's last London date."""
    return latest_minute_london is not None and latest_minute_london.date() > window.end


def league_from_daily(
    window: Window,
    lines: Sequence[LineInfo],
    daily: Sequence[DailyRow],
    incidents: Sequence[IncidentRow],
    latest_minute_london: datetime | None = None,
    monday_morning: Sequence[MondayMorningRow] = (),
    measured_daily: Sequence[MeasuredDailyRow] = (),
) -> League:
    """Sum fct_line_daily over a custom window and rank it (D20, D22).

    The measured view sums fct_line_measured_daily over the same dates and
    applies fct_line_weekly's rules (D51 to D53).

    Rank movement and Line of the Week are left empty: there is no earlier
    window of the same shape to compare with.
    """
    in_window = [d for d in daily if window.start <= d.london_date <= window.end]
    window_incidents = [i for i in incidents if window.start <= i.start_london_date <= window.end]
    daily_by_line = rows_by_line(in_window)
    incidents_by_line = rows_by_line(window_incidents)
    measured_by_line = rows_by_line(
        [m for m in measured_daily if window.start <= m.london_date <= window.end]
    )
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
            time_weighted_scored_minutes=sum(r.time_weighted_scored_minutes for r in rows),
            incidents=sum(r.incidents for r in rows),
            median_recovery_minutes=_median_recovery(incidents_by_line.get(line.line_id, [])),
        )
        measured_rows = measured_by_line.get(line.line_id, [])
        sums = sum((m.sums for m in measured_rows), MeasuredSums()) if measured_rows else None
        withheld = measured_withheld_reason(line, sums)
        standings.append(
            replace(
                standing,
                provisional_reasons=provisional_reasons(standing),
                measured=None
                if withheld is not None or sums is None
                else measured_view(line, sums, None),
                measured_withheld_reason=withheld,
            )
        )
    return League(
        window=window,
        standings=tuple(rank_standings(rank_measured(standings))),
        worst_incident=worst_incident(lines, window_incidents),
        is_complete=data_reaches_past(window, latest_minute_london),
        latest_data_london=latest_minute_london,
        line_of_the_week=LineOfTheWeek(None, null_reason="custom_window"),
        monday_morning=monday_mornings(window, lines, monday_morning),
    )


def league_from_weekly(
    week_start: date,
    lines: Sequence[LineInfo],
    weekly: Sequence[WeeklyRow],
    incidents: Sequence[IncidentRow],
    latest_minute_london: datetime | None = None,
    monday_morning: Sequence[MondayMorningRow] = (),
) -> League:
    """The ISO week as fct_line_weekly ranked and flagged it."""
    window = Window(start=week_start, end=week_start + timedelta(days=6))
    rows = {w.line_id: w for w in weekly if w.week_start == week_start}
    standings = []
    for line in lines:
        row = rows.get(line.line_id)
        if row is None:
            standing = Standing(line, 0, 0, 0, 0, 0, 0.0, 0.0, 0, None)
        else:
            standing = Standing(
                line=line,
                scheduled_minutes=row.scheduled_minutes,
                observed_minutes=row.observed_minutes,
                engineering_works_minutes=row.engineering_works_minutes,
                scored_minutes=row.scored_minutes,
                good_service_minutes=row.good_service_minutes,
                weighted_disruption_minutes=row.weighted_disruption_minutes,
                time_weighted_scored_minutes=row.time_weighted_scored_minutes,
                incidents=row.incidents,
                median_recovery_minutes=row.median_recovery_minutes,
                rank=row.disruption_rank,
                rank_movement=row.rank_movement,
                measured=None
                if row.measured_withheld_reason is not None or row.measured_sums is None
                else measured_view(line, row.measured_sums, row.experienced_rank),
                measured_withheld_reason=row.measured_withheld_reason
                if row.measured_sums is not None or row.measured_withheld_reason is not None
                else "line_not_measured",
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
        line_of_the_week=line_of_the_week(week_start, lines, weekly),
        monday_morning=monday_mornings(window, lines, monday_morning),
    )
