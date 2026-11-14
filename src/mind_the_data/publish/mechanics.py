"""League mechanics beside the weekly table (M6): recovery, strikes, divisions, seasons.

Pure functions over mart rows and seeds. The Recovery Time table is worked
out here from incident rows for any window, mirroring rpt_recovery_time.
Divisions, seasons and the strikes-excluded league are read as dbt built
them and only shaped here.
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

from mind_the_data.publish.marts import (
    DivisionMonthRow,
    IncidentRow,
    LineInfo,
    SeedingRow,
    StrikesExcludedRow,
    WeeklyRow,
)

# D18 (incidents and recovery) is open, so every recovery number is provisional.
RECOVERY_DEFINITION_STATUS = "provisional_d18"
# Statuses that take a line's dates out of the strikes-excluded league (D60).
EXCLUDING_STATUSES = frozenset({"held", "announced"})


@dataclass(frozen=True)
class RecoveryEntry:
    """A line's incidents and recovery in a window (D59)."""

    line: LineInfo
    incidents: int
    recovered_incidents: int
    median_recovery_minutes: float | None
    mean_recovery_minutes: float | None
    longest_recovery_minutes: int | None
    rank: int | None = None

    @property
    def open_incidents(self) -> int:
        return self.incidents - self.recovered_incidents

    @property
    def unranked_reason(self) -> str | None:
        return None if self.recovered_incidents else "no_recovered_incidents"


def recovery_table(
    lines: Sequence[LineInfo], incidents: Sequence[IncidentRow]
) -> tuple[RecoveryEntry, ...]:
    """Median recovery per line, quickest first (D59, provisional on D18).

    Equal medians go to fewer incidents; still-equal lines share a rank, as
    SQL rank() in rpt_recovery_time. Lines with no closed incident follow,
    unranked, by name.
    """
    entries = []
    for line in lines:
        own = [i for i in incidents if i.line_id == line.line_id]
        recovered = [i.recovery_minutes for i in own if i.recovery_minutes is not None]
        entries.append(
            RecoveryEntry(
                line=line,
                incidents=len(own),
                recovered_incidents=len(recovered),
                median_recovery_minutes=float(median(recovered)) if recovered else None,
                mean_recovery_minutes=float(mean(recovered)) if recovered else None,
                longest_recovery_minutes=max(recovered, default=None),
            )
        )

    def key(e: RecoveryEntry) -> tuple[float, int]:
        return (e.median_recovery_minutes or 0.0, e.incidents)

    ranked = sorted(
        (e for e in entries if e.recovered_incidents), key=lambda e: (key(e), e.line.line_id)
    )
    out: list[RecoveryEntry] = []
    previous: tuple[float, int] | None = None
    current = 0
    for position, entry in enumerate(ranked, start=1):
        if key(entry) != previous:
            current, previous = position, key(entry)
        out.append(replace(entry, rank=current))
    out.extend(
        sorted((e for e in entries if not e.recovered_incidents), key=lambda e: e.line.line_name)
    )
    return tuple(out)


@dataclass(frozen=True)
class IndustrialAction:
    """One row of seed_industrial_action, with its source (D60)."""

    action_id: str
    union_name: str
    applies_to: str
    applies_to_id: str
    start_london_date: date
    end_london_date: date
    status: str
    description: str
    source_publisher: str
    source_title: str
    source_date: date
    source_url: str

    @property
    def excludes_days(self) -> bool:
        return self.status in EXCLUDING_STATUSES

    def applies_to_line(self, line: LineInfo) -> bool:
        if self.applies_to == "mode":
            return line.mode_name == self.applies_to_id
        return line.line_id == self.applies_to_id

    def overlaps(self, start: date, end: date) -> bool:
        return self.start_london_date <= end and self.end_london_date >= start


def load_industrial_action(transform_dir: Path) -> tuple[IndustrialAction, ...]:
    with (transform_dir / "seeds" / "seed_industrial_action.csv").open(newline="") as f:
        return tuple(
            IndustrialAction(
                action_id=r["action_id"],
                union_name=r["union_name"],
                applies_to=r["applies_to"],
                applies_to_id=r["applies_to_id"],
                start_london_date=date.fromisoformat(r["start_london_date"]),
                end_london_date=date.fromisoformat(r["end_london_date"]),
                status=r["status"],
                description=r["description"],
                source_publisher=r["source_publisher"],
                source_title=r["source_title"],
                source_date=date.fromisoformat(r["source_date"]),
                source_url=r["source_url"],
            )
            for r in csv.DictReader(f)
        )


@dataclass(frozen=True)
class StrikesExcluded:
    """The week's strikes-excluded league (D60), in rank order."""

    rows: tuple[StrikesExcludedRow, ...]

    @property
    def is_same_as_main(self) -> bool:
        return not any(r.week_has_industrial_action for r in self.rows)


def strikes_excluded_for_week(
    week_start: date, rows: Sequence[StrikesExcludedRow]
) -> StrikesExcluded | None:
    """The variant's rows for the week, or None when dbt has none for it."""
    week = [r for r in rows if r.week_start == week_start]
    if not week:
        return None
    week.sort(key=lambda r: (r.disruption_rank is None, r.disruption_rank or 0, r.line_id))
    return StrikesExcluded(rows=tuple(week))


# ---- Divisions -----------------------------------------------------------

DIVISION_NAMES = {1: "Division One", 2: "Division Two"}


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def month_end(first: date) -> date:
    return next_month(first) - timedelta(days=1)


def months_between(first: date, last: date) -> list[date]:
    months, current = [], month_start(first)
    while current <= last:
        months.append(current)
        current = next_month(current)
    return months


def complete_weeks(weekly: Sequence[WeeklyRow], threshold: float) -> list[date]:
    """Weeks every line has finished, with at least `threshold` of minutes observed.

    The rule rpt_division_seeding seeds from (and D47 compares weeks with).
    """
    by_week: dict[date, list[WeeklyRow]] = {}
    for row in weekly:
        by_week.setdefault(row.week_start, []).append(row)
    out = []
    for week, rows in sorted(by_week.items()):
        scheduled = sum(r.scheduled_minutes for r in rows)
        observed = sum(r.observed_minutes for r in rows)
        if (
            all(r.is_week_complete for r in rows)
            and scheduled
            and observed / scheduled >= threshold
        ):
            out.append(week)
    return out


@dataclass(frozen=True)
class DivisionState:
    """What divisions/{yyyy-mm}.json says for one month."""

    month: date
    # not_seeded, seeded (divisions start in a later month), pending (the month
    # is not built yet), in_progress or complete
    status: str
    seeding: tuple[SeedingRow, ...]
    rows: tuple[DivisionMonthRow, ...]
    complete_weeks_available: int

    @property
    def first_division_month(self) -> date | None:
        return self.seeding[0].first_division_month if self.seeding else None


def division_state(
    month: date,
    seeding: Sequence[SeedingRow],
    months: Sequence[DivisionMonthRow],
    complete_weeks_available: int,
) -> DivisionState:
    """The month's divisions, or the explicit state before they play."""
    ordered_seeding = tuple(sorted(seeding, key=lambda r: r.seed_position))
    rows = tuple(
        sorted(
            (r for r in months if r.month_start == month),
            key=lambda r: (r.division, r.division_rank is None, r.division_rank or 0, r.line_id),
        )
    )
    if not ordered_seeding:
        status = "not_seeded"
    elif not rows:
        # Before the first division month the seeding is all there is; on or
        # after it, the month has not been built yet (dbt is behind).
        first = ordered_seeding[0].first_division_month
        status = "seeded" if month < first else "pending"
    elif all(r.is_month_complete for r in rows):
        status = "complete"
    else:
        status = "in_progress"
    return DivisionState(
        month=month,
        status=status,
        seeding=ordered_seeding,
        rows=rows,
        # Once seeded, dbt's count is the one the seeding status rests on.
        complete_weeks_available=ordered_seeding[0].complete_weeks_available
        if ordered_seeding
        else complete_weeks_available,
    )
