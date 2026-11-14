"""Build the published JSON documents (PLAN 8) from league and mart rows.

Pure functions: the caller supplies generated_at and git_sha, so the same
input always gives the same document. Every document carries the common
envelope: schema_version, generated_at, data_window, completeness, git_sha.
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from mind_the_data.publish.insight import Insight
from mind_the_data.publish.league import (
    CLOSURE_SHARE_PROVISIONAL,
    MIN_OPEN_SHARE_TO_RANK,
    OPEN_METRIC_DECISIONS,
    SETTLED_METRIC_DECISIONS,
    League,
    MeasuredView,
    MondayMorning,
    Standing,
    Window,
    measured_view,
)
from mind_the_data.publish.marts import BadHourRow, LineInfo, SeasonRow, WeeklyRow
from mind_the_data.publish.mechanics import (
    DIVISION_NAMES,
    RECOVERY_DEFINITION_STATUS,
    DivisionState,
    IndustrialAction,
    month_end,
)
from mind_the_data.publish.schemas import SCHEMA_VERSION

TYPICAL_BAD_HOURS = 3
# An hour of day needs this many scored minutes across the history before it
# can be named a typical bad hour, so one disrupted quarter-hour on a line
# that is mostly closed is not reported as a pattern (D37).
MIN_BAD_HOUR_SCORED_MINUTES = 300


@dataclass(frozen=True)
class Stamp:
    generated_at: datetime
    git_sha: str

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")


def _utc(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(ts: datetime) -> str:
    """A London wall-clock time from the marts, written without an offset."""
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def iso_week_of(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def envelope(stamp: Stamp, window: Window, completeness: float | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc(stamp.generated_at),
        "data_window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": "Europe/London",
            "iso_week": window.iso_week,
        },
        "completeness": _round(completeness, 4),
        "git_sha": stamp.git_sha,
    }


def measured_published(league: League) -> bool:
    return any(s.measured is not None for s in league.standings)


def measured_document(measured: MeasuredView | None) -> dict[str, Any] | None:
    """The published measured numbers (PLAN 7.3, D42), or None when withheld."""
    if measured is None:
        return None
    return {
        "excess_wait_minutes": _round(measured.excess_wait_minutes, 2),
        "long_gaps_per_100_hours": _round(measured.long_gaps_per_100_hours, 2),
        "honesty_gap_hours": _round(measured.honesty_gap_hours, 1),
        "honesty_gap_share": _round(measured.honesty_gap_share, 4),
        "experienced_rank": measured.experienced_rank,
        "trains_run_share": _round(measured.trains_run_share, 4),
        "trains_run_withheld_reason": measured.trains_run_withheld_reason,
        "measured_hours": measured.measured_hours,
        "measured_share": _round(measured.measured_share, 4),
        "gate_pass_share": _round(measured.gate_pass_share, 4),
        "good_service_measured_hours": measured.good_service_measured_hours,
        "is_weak_evidence": measured.is_weak_evidence,
    }


def measured_status(standing: Standing) -> str:
    """`measured`, `withheld` (a measured line whose week did not qualify) or `not_measured` (D43)."""
    if standing.measured is not None:
        return "measured"
    if standing.measured_withheld_reason in (None, "line_not_measured"):
        return "not_measured"
    return "withheld"


def _standing(standing: Standing) -> dict[str, Any]:
    measured = standing.measured
    return {
        "line_id": standing.line.line_id,
        "line_name": standing.line.line_name,
        "mode": standing.line.mode_name,
        "colour": standing.line.colour,
        "is_low_volume": standing.line.is_low_volume,
        "rank": standing.rank,
        "unranked_reason": standing.unranked_reason,
        "rank_movement": standing.rank_movement,
        "disruption_index": _round(standing.disruption_index, 1),
        "good_service_pct": _round(standing.good_service_pct, 2),
        "incidents": standing.incidents,
        "incidents_per_100_hours": _round(standing.incidents_per_100_hours, 2),
        "median_recovery_minutes": _round(standing.median_recovery_minutes, 1),
        "completeness": _round(standing.completeness, 4),
        "scheduled_minutes": standing.scheduled_minutes,
        "scored_minutes": standing.scored_minutes,
        "engineering_works_minutes": standing.engineering_works_minutes,
        "engineering_works_hours": _round(standing.engineering_works_hours, 1),
        "is_provisional": standing.is_provisional,
        "provisional_reasons": [
            {"code": r.code, "decisions": list(r.decisions), "detail": r.detail}
            for r in standing.provisional_reasons
        ],
        "measured_status": measured_status(standing),
        "measured_withheld_reason": None
        if measured is not None
        else standing.measured_withheld_reason,
        "measured": measured_document(measured),
    }


def _monday(entry: MondayMorning) -> dict[str, Any]:
    row = entry.row
    return {
        "line_id": entry.line.line_id,
        "line_name": entry.line.line_name,
        "monday": row.week_start.isoformat(),
        "rank": row.monday_morning_rank,
        "unranked_reason": row.unranked_reason,
        "monday_morning_index": _round(row.monday_morning_index, 1),
        "good_service_pct": _round(row.good_service_pct, 2),
        "completeness": _round(row.completeness, 4),
        "is_provisional": row.is_provisional,
    }


def highlights_document(league: League) -> dict[str, Any]:
    """The week's Wooden Spoon and Line of the Week (M6); the worst incident sits beside them."""
    spoon = league.wooden_spoon
    best = league.line_of_the_week
    return {
        "wooden_spoon": None
        if spoon is None
        else {
            "line_id": spoon.standing.line.line_id,
            "line_name": spoon.standing.line.line_name,
            "rank": spoon.standing.rank,
            "disruption_index": _round(spoon.standing.disruption_index, 1),
            "is_provisional": spoon.standing.is_provisional,
            "shared_by": spoon.shared_by,
        },
        "line_of_the_week": None
        if best.line is None
        else {
            "line_id": best.line.line_id,
            "line_name": best.line.line_name,
            "disruption_index": _round(best.disruption_index, 1),
            "previous_disruption_index": _round(best.previous_disruption_index, 1),
            "improvement": _round(best.improvement, 1),
        },
        "line_of_the_week_null_reason": best.null_reason,
    }


def engineering_works_document(league: League) -> list[dict[str, Any]]:
    """Lines with hours closed for works in the window, most first (PLAN 7.4)."""
    closed = sorted(
        (s for s in league.standings if s.engineering_works_minutes > 0),
        key=lambda s: (-s.engineering_works_minutes, s.line.line_name),
    )
    return [
        {
            "line_id": s.line.line_id,
            "line_name": s.line.line_name,
            "engineering_works_hours": _round(s.engineering_works_hours, 1),
            "engineering_works_share": _round(s.closure_share, 4),
            "is_unranked_for_works": s.unranked_reason == "engineering_works",
        }
        for s in closed
    ]


def recovery_document(league: League) -> dict[str, Any]:
    """The Recovery Time table (D59), labelled provisional while D18 is open."""
    return {
        "definition_status": RECOVERY_DEFINITION_STATUS,
        "decisions": ["D18", "D59"],
        "lines": [
            {
                "line_id": e.line.line_id,
                "line_name": e.line.line_name,
                "rank": e.rank,
                "unranked_reason": e.unranked_reason,
                "incidents": e.incidents,
                "recovered_incidents": e.recovered_incidents,
                "open_incidents": e.open_incidents,
                "median_recovery_minutes": _round(e.median_recovery_minutes, 1),
                "mean_recovery_minutes": _round(e.mean_recovery_minutes, 1),
                "longest_recovery_minutes": e.longest_recovery_minutes,
            }
            for e in league.recovery_time
        ],
    }


def action_document(action: IndustrialAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "union": action.union_name,
        "applies_to": action.applies_to,
        "applies_to_id": action.applies_to_id,
        "start_london_date": action.start_london_date.isoformat(),
        "end_london_date": action.end_london_date.isoformat(),
        "status": action.status,
        "excludes_days": action.excludes_days,
        "description": action.description,
        "source": {
            "publisher": action.source_publisher,
            "title": action.source_title,
            "date": action.source_date.isoformat(),
            "url": action.source_url,
        },
    }


def strikes_excluded_document(league: League) -> dict[str, Any] | None:
    """The strikes-excluded league (PLAN 7.4, D60); None for a custom window or before dbt built it."""
    variant = league.strikes_excluded
    if variant is None:
        return None
    names = {s.line.line_id: s.line.line_name for s in league.standings}
    return {
        "is_same_as_main": variant.is_same_as_main,
        "lines": [
            {
                "line_id": r.line_id,
                "line_name": names.get(r.line_id, r.line_id),
                "rank": r.disruption_rank,
                "unranked_reason": r.unranked_reason,
                "disruption_index": _round(r.disruption_index, 1),
                "good_service_pct": _round(r.good_service_pct, 2),
                "strike_days": r.strike_days,
                "completeness": _round(r.completeness, 4),
                "is_provisional": r.is_provisional,
            }
            for r in variant.rows
            if r.line_id in names
        ],
    }


def league_document(league: League, insight: Insight | None, stamp: Stamp) -> dict[str, Any]:
    worst = league.worst_incident
    return {
        **envelope(stamp, league.window, league.completeness),
        "is_window_complete": league.is_complete,
        "data_through": None
        if league.latest_data_london is None
        else _local(league.latest_data_london),
        "views": {
            "declared": "published",
            "measured": "published" if measured_published(league) else "not_measured",
        },
        "open_decisions": list(league.open_decisions),
        "lines": [_standing(s) for s in league.standings],
        "highlights": highlights_document(league),
        "monday_morning": [_monday(m) for m in league.monday_morning],
        "engineering_works": engineering_works_document(league),
        "recovery_time": recovery_document(league),
        "industrial_action": [action_document(a) for a in league.industrial_action],
        "strikes_excluded": strikes_excluded_document(league),
        "worst_incident": None
        if worst is None
        else {
            "line_id": worst.line.line_id,
            "line_name": worst.line.line_name,
            "started_at": _utc(worst.incident.started_at_utc.replace(tzinfo=UTC)),
            "started_at_london": _local(worst.incident.started_at_london),
            "start_london_date": worst.incident.start_london_date.isoformat(),
            "duration_minutes": worst.incident.duration_minutes,
            "severity_weighted_minutes": _round(worst.incident.severity_weighted_minutes, 1),
            "is_open": worst.incident.is_open,
            "worst_status": worst.incident.worst_status_description,
            "first_reason": worst.incident.first_reason,
        },
        "insight": None
        if insight is None
        else {"kind": insight.kind, "line_id": insight.line_id, "text": insight.text},
    }


def _week_measured(line: LineInfo, row: WeeklyRow) -> MeasuredView | None:
    if row.measured_withheld_reason is not None or row.measured_sums is None:
        return None
    return measured_view(line, row.measured_sums, row.experienced_rank)


def _week(line: LineInfo, row: WeeklyRow) -> dict[str, Any]:
    measured = _week_measured(line, row)
    return {
        "iso_week": iso_week_of(row.week_start),
        "week_start": row.week_start.isoformat(),
        "week_end": (row.week_start + timedelta(days=6)).isoformat(),
        "rank": row.disruption_rank,
        "unranked_reason": row.unranked_reason,
        "rank_movement": row.rank_movement,
        "disruption_index": _round(row.disruption_index, 1),
        "good_service_pct": _round(row.good_service_pct, 2),
        "incidents": row.incidents,
        "engineering_works_hours": round(row.engineering_works_minutes / 60, 1),
        "completeness": _round(row.completeness, 4),
        "is_week_complete": row.is_week_complete,
        "is_provisional": row.is_provisional,
        "measured_withheld_reason": None
        if measured is not None
        else row.measured_withheld_reason or "line_not_measured",
        "measured": measured_document(measured),
    }


def typical_bad_hours(rows: Sequence[BadHourRow], top: int = TYPICAL_BAD_HOURS) -> list[Any]:
    """The London hours with the highest disrupted share; hours never disrupted are left out."""
    disrupted = [
        r
        for r in rows
        if r.scored_minutes >= MIN_BAD_HOUR_SCORED_MINUTES and r.disrupted_minutes > 0
    ]
    disrupted.sort(key=lambda r: (-r.disrupted_minutes / r.scored_minutes, r.london_hour))
    return [
        {
            "london_hour": r.london_hour,
            "disrupted_share": round(r.disrupted_minutes / r.scored_minutes, 4),
            "scored_minutes": r.scored_minutes,
        }
        for r in disrupted[:top]
    ]


def line_document(
    line: LineInfo,
    weekly: Sequence[WeeklyRow],
    bad_hours: Sequence[BadHourRow],
    stamp: Stamp,
) -> dict[str, Any] | None:
    """lines/{line_id}.json, or None when the line has no weeks yet."""
    # Weeks before the collector started have scheduled minutes but nothing observed.
    history = sorted(
        (w for w in weekly if w.line_id == line.line_id and w.observed_minutes > 0),
        key=lambda w: w.week_start,
    )
    if not history:
        return None
    window = Window(start=history[0].week_start, end=history[-1].week_start + timedelta(days=6))
    scheduled = sum(w.scheduled_minutes for w in history)
    observed = sum(w.observed_minutes for w in history)
    complete = [w for w in history if w.is_week_complete and w.disruption_index is not None]
    worst = max(complete, key=lambda w: (w.disruption_index or 0.0, w.week_start), default=None)
    return {
        **envelope(stamp, window, observed / scheduled if scheduled else None),
        "line": {
            "line_id": line.line_id,
            "line_name": line.line_name,
            "mode": line.mode_name,
            "colour": line.colour,
            "is_low_volume": line.is_low_volume,
        },
        "history": [_week(line, w) for w in history],
        "worst_week": None if worst is None else _week(line, worst),
        "typical_bad_hours": typical_bad_hours([r for r in bad_hours if r.line_id == line.line_id]),
        "measured_status": "measured"
        if any(_week_measured(line, w) is not None for w in history)
        else "not_measured",
    }


def _bool(text: str) -> bool:
    return text.strip().lower() == "true"


def dbt_vars(transform_dir: Path) -> dict[str, Any]:
    project = yaml.safe_load((transform_dir / "dbt_project.yml").read_text())
    variables: dict[str, Any] = project["vars"]
    return variables


def methodology_document(
    transform_dir: Path,
    league: League,
    stamp: Stamp,
    *,
    sensitivity: dict[str, Any] | None = None,
    industrial_action: Sequence[IndustrialAction] = (),
) -> dict[str, Any]:
    """Weights and thresholds read from the seeds and dbt vars this commit builds with.

    `sensitivity` is the PLAN 7.5 block (publish.sensitivity); without it the
    weight sets are listed with no weeks.
    """
    seeds = transform_dir / "seeds"
    with (seeds / "severity_weights.csv").open() as f:
        severity = [
            {
                "severity_code": int(r["severity_code"]),
                "description": r["description"],
                "weight_unplanned": float(r["weight_unplanned"]),
                "weight_planned": float(r["weight_planned"]),
                "is_closure": _bool(r["is_closure"]),
                "is_service_closed": _bool(r["is_service_closed"]),
                "source": r["source"],
            }
            for r in csv.DictReader(f)
        ]
    with (seeds / "time_bands.csv").open() as f:
        bands = [
            {
                "day_class": r["day_class"],
                "time_band": r["time_band"],
                "start_minute": int(r["band_start_minute"]),
                "end_minute": int(r["band_end_minute"]),
                "time_weight": float(r["time_weight"]),
            }
            for r in csv.DictReader(f)
        ]
    variables = dbt_vars(transform_dir)
    max_weight = max(max(w["weight_unplanned"], w["weight_planned"]) for w in severity)
    return {
        **envelope(stamp, league.window, league.completeness),
        "severity_weights": severity,
        "time_bands": bands,
        "disruption_index": {
            "definition": (
                "100 x the sum of severity weight x time weight over scored minutes, "
                "divided by the sum of time weight over the same minutes (D19)"
            ),
            "min": 0.0,
            "max": 100 * max_weight,
        },
        "thresholds": {
            "provisional_completeness": float(variables["provisional_completeness"]),
            "closure_share_provisional": CLOSURE_SHARE_PROVISIONAL,
            "min_open_share_to_rank": MIN_OPEN_SHARE_TO_RANK,
            "incident_merge_gap_minutes": int(variables["incident_merge_gap_minutes"]),
            "gate_low_ratio": float(variables["gate_low_ratio"]),
            "gate_high_ratio": float(variables["gate_high_ratio"]),
            "gate_max_out_of_band_share": float(variables["gate_max_out_of_band_share"]),
            "min_measured_share": float(variables["min_measured_share"]),
        },
        "measured_view_withheld_lines": list(variables["measured_view_withheld_lines"]),
        "trains_run_withheld_lines": list(variables["trains_run_withheld_lines"]),
        "views": {
            "declared": "published",
            "measured": "published" if measured_published(league) else "not_measured",
        },
        "divisions": {
            "seeding_weeks": int(variables["division_seeding_weeks"]),
            "full_seeding_weeks": int(variables["division_full_seeding_weeks"]),
            "division_one_size": int(variables["division_one_size"]),
            "moves_per_month": int(variables["division_moves"]),
            "definition": (
                "Seeded by Disruption Index over the first complete weeks (ADR-015); "
                "each month the bottom ranked lines of Division One swap with the top "
                "ranked lines of Division Two; unranked lines do not move (D56, D57)"
            ),
        },
        "season": {
            "starts": "04-01",
            "definition": "Declared-view standings from 1 April to 31 March, London dates (D58)",
        },
        "recovery_time": {
            "definition_status": RECOVERY_DEFINITION_STATUS,
            "definition": (
                "Minutes from an incident's first to its last incident minute; open "
                "incidents are left out of the median. Provisional while D18 is open (D59)"
            ),
        },
        "industrial_action": [action_document(a) for a in industrial_action],
        "sensitivity": sensitivity
        if sensitivity is not None
        else {"method": "not computed", "weight_sets": [], "weeks": []},
        "open_decisions": list(OPEN_METRIC_DECISIONS),
        "settled_decisions": list(SETTLED_METRIC_DECISIONS),
    }


def divisions_document(
    state: DivisionState, lines: Sequence[LineInfo], stamp: Stamp
) -> dict[str, Any]:
    """divisions/{yyyy-mm}.json: the month's divisions, or the explicit state before them."""
    names = {line.line_id: line.line_name for line in lines}
    window = Window(start=state.month, end=month_end(state.month))
    scheduled = sum(r.scheduled_minutes for r in state.rows)
    observed = sum(r.observed_minutes for r in state.rows)
    seeding = state.seeding
    first = seeding[0] if seeding else None
    return {
        **envelope(stamp, window, observed / scheduled if scheduled else None),
        "month": state.month.strftime("%Y-%m"),
        "status": state.status,
        "complete_weeks_available": state.complete_weeks_available,
        "seeding": None
        if first is None
        else {
            "status": first.seeding_status,
            "weeks": first.seeding_weeks,
            "start": first.seeding_start.isoformat(),
            "end": first.seeding_end.isoformat(),
            "first_division_month": first.first_division_month.strftime("%Y-%m"),
            "lines": [
                {
                    "line_id": r.line_id,
                    "line_name": names.get(r.line_id, r.line_id),
                    "seed_position": r.seed_position,
                    "division": r.division,
                    "disruption_index": _round(r.disruption_index, 1),
                    "good_service_pct": _round(r.good_service_pct, 2),
                    "unranked_reason": r.unranked_reason,
                }
                for r in seeding
            ],
        },
        "divisions": [
            {
                "division": division,
                "name": DIVISION_NAMES[division],
                "lines": [
                    {
                        "line_id": r.line_id,
                        "line_name": names.get(r.line_id, r.line_id),
                        "rank": r.division_rank,
                        "unranked_reason": r.unranked_reason,
                        "joined_by": r.joined_by,
                        "disruption_index": _round(r.disruption_index, 1),
                        "good_service_pct": _round(r.good_service_pct, 2),
                        "completeness": _round(r.completeness, 4),
                        "is_provisional": r.is_provisional,
                        "movement": r.movement,
                        "next_division": r.next_division,
                    }
                    for r in state.rows
                    if r.division == division
                ],
            }
            for division in sorted({r.division for r in state.rows})
        ],
        "open_decisions": list(OPEN_METRIC_DECISIONS),
    }


def season_document(
    season: str, rows: Sequence[SeasonRow], lines: Sequence[LineInfo], stamp: Stamp
) -> dict[str, Any]:
    """season/{season}.json: the season's standings so far (D58)."""
    by_id = {line.line_id: line for line in lines}
    own = sorted(
        (r for r in rows if r.season == season and r.line_id in by_id),
        key=lambda r: (r.season_rank is None, r.season_rank or 0, by_id[r.line_id].line_name),
    )
    if not own:
        raise ValueError(f"no rows for season {season}")
    observed_dates = [r.first_observed_date for r in own if r.first_observed_date] + [
        r.last_observed_date for r in own if r.last_observed_date
    ]
    window = Window(
        start=min(observed_dates, default=own[0].season_start),
        end=max(observed_dates, default=own[0].season_start),
    )
    scheduled = sum(r.scheduled_minutes for r in own)
    observed = sum(r.observed_minutes for r in own)
    return {
        **envelope(stamp, window, observed / scheduled if scheduled else None),
        "season": season,
        "season_start": own[0].season_start.isoformat(),
        "season_end": own[0].season_end.isoformat(),
        "is_season_complete": all(r.is_season_complete for r in own),
        "lines": [
            {
                "line_id": r.line_id,
                "line_name": by_id[r.line_id].line_name,
                "rank": r.season_rank,
                "unranked_reason": r.unranked_reason,
                "disruption_index": _round(r.disruption_index, 1),
                "good_service_pct": _round(r.good_service_pct, 2),
                "incidents": r.incidents,
                "incidents_per_100_hours": _round(r.incidents_per_100_hours, 2),
                "engineering_works_hours": round(r.engineering_works_minutes / 60, 1),
                "completeness": _round(r.completeness, 4),
                "is_provisional": r.is_provisional,
            }
            for r in own
        ],
        "open_decisions": list(OPEN_METRIC_DECISIONS),
    }


def index_document(
    archive: Sequence[League],
    lines: Sequence[LineInfo],
    stamp: Stamp,
    *,
    fallback_window: Window,
    latest_data_london: datetime | None,
    division_months: Sequence[DivisionState] = (),
    seasons: Sequence[str] = (),
) -> dict[str, Any]:
    """meta/index.json: the archived weeks, the latest week and the lines with history files.

    archive holds the complete weekly leagues written in this run; lines the
    lines that got a lines/{line_id}.json. fallback_window stands in for the
    data window before the first complete week.
    """
    weeks = sorted(archive, key=lambda league: league.window.start, reverse=True)
    if weeks:
        window = Window(start=weeks[-1].window.start, end=weeks[0].window.end)
        scheduled = sum(s.scheduled_minutes for lg in weeks for s in lg.standings)
        observed = sum(s.observed_minutes for lg in weeks for s in lg.standings)
        completeness = observed / scheduled if scheduled else None
    else:
        window, completeness = fallback_window, None
    measured = any(measured_published(league) for league in weeks)
    return {
        **envelope(stamp, window, completeness),
        "data_through": None if latest_data_london is None else _local(latest_data_london),
        "latest_week": iso_week_of(weeks[0].window.start) if weeks else None,
        "weeks": [
            {
                "iso_week": iso_week_of(league.window.start),
                "week_start": league.window.start.isoformat(),
                "week_end": league.window.end.isoformat(),
                "path": f"league/{iso_week_of(league.window.start)}.json",
            }
            for league in weeks
        ],
        "lines": [
            {
                "line_id": line.line_id,
                "line_name": line.line_name,
                "mode": line.mode_name,
                "colour": line.colour,
                "is_low_volume": line.is_low_volume,
                "path": f"lines/{line.line_id}.json",
            }
            for line in sorted(lines, key=lambda line: (line.line_name, line.line_id))
        ],
        "months": [
            {
                "month": state.month.strftime("%Y-%m"),
                "status": state.status,
                "path": f"divisions/{state.month.strftime('%Y-%m')}.json",
            }
            for state in sorted(division_months, key=lambda s: s.month, reverse=True)
        ],
        "seasons": [
            {"season": season, "path": f"season/{season}.json"}
            for season in sorted(seasons, reverse=True)
        ],
        "views": {
            "declared": "published",
            "measured": "published" if measured else "not_measured",
        },
        "open_decisions": list(OPEN_METRIC_DECISIONS),
    }
