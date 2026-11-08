"""Rows read from the dbt marts, and the two places they come from.

`AthenaMarts` runs read-only SELECTs against the prod Glue database through
the dev workgroup; `RecordedMarts` replays the same queries' results from JSON
files, so the export and the cards are tested without AWS. Both return the
raw string cells Athena gives back, parsed here into typed rows.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from mind_the_data.ops.athena import run_query

if TYPE_CHECKING:
    from mypy_boto3_athena import AthenaClient

Cells = Mapping[str, str | None]


@dataclass(frozen=True)
class LineInfo:
    line_id: str
    line_name: str
    mode_name: str
    colour: str
    is_low_volume: bool


@dataclass(frozen=True)
class DailyRow:
    """One row of fct_line_daily: minute counts for a line and London date."""

    line_id: str
    london_date: date
    scheduled_minutes: int
    observed_minutes: int
    missing_minutes: int
    engineering_works_minutes: int
    service_closed_minutes: int
    scored_minutes: int
    good_service_minutes: int
    disrupted_minutes: int
    weighted_disruption_minutes: float
    incidents: int


@dataclass(frozen=True)
class WeeklyRow:
    """One row of fct_line_weekly, metrics and rank as dbt computed them."""

    line_id: str
    week_start: date
    scheduled_minutes: int
    observed_minutes: int
    engineering_works_minutes: int
    scored_minutes: int
    good_service_minutes: int
    weighted_disruption_minutes: float
    incidents: int
    median_recovery_minutes: float | None
    completeness: float
    disruption_index: float | None
    good_service_pct: float | None
    incidents_per_100_hours: float | None
    is_week_complete: bool
    is_provisional: bool
    disruption_rank: int | None
    rank_movement: int | None


@dataclass(frozen=True)
class IncidentRow:
    line_id: str
    started_at_utc: datetime
    start_london_date: date
    duration_minutes: int
    is_open: bool
    recovery_minutes: int | None
    peak_severity_weight: float
    worst_status_description: str
    first_reason: str | None


@dataclass(frozen=True)
class BadHourRow:
    """Share of a line's scored minutes that were disrupted, per London hour of day."""

    line_id: str
    london_hour: int
    scored_minutes: int
    disrupted_minutes: int


class Marts(Protocol):
    def lines(self) -> list[LineInfo]: ...
    def daily(self, start: date, end: date) -> list[DailyRow]: ...
    def weekly(self) -> list[WeeklyRow]: ...
    def incidents(self, start: date, end: date) -> list[IncidentRow]: ...
    def bad_hours(self, start: date, end: date) -> list[BadHourRow]: ...


# ---- Parsing ---------------------------------------------------------------


def _req(cells: Cells, name: str) -> str:
    value = cells.get(name)
    if value is None or value == "":
        raise ValueError(f"column {name} is null or missing")
    return value


def _opt(cells: Cells, name: str) -> str | None:
    value = cells.get(name)
    return None if value is None or value == "" else value


def _int(cells: Cells, name: str) -> int:
    return int(float(_req(cells, name)))


def _opt_int(cells: Cells, name: str) -> int | None:
    value = _opt(cells, name)
    return None if value is None else int(float(value))


def _float(cells: Cells, name: str) -> float:
    return float(_req(cells, name))


def _opt_float(cells: Cells, name: str) -> float | None:
    value = _opt(cells, name)
    return None if value is None else float(value)


def _bool(cells: Cells, name: str) -> bool:
    value = _req(cells, name).lower()
    if value not in ("true", "false"):
        raise ValueError(f"column {name} is not a boolean: {value!r}")
    return value == "true"


def _date(cells: Cells, name: str) -> date:
    return date.fromisoformat(_req(cells, name)[:10])


def _timestamp(cells: Cells, name: str) -> datetime:
    # Athena prints timestamp(6) as 'YYYY-MM-DD HH:MM:SS.ffffff', in UTC.
    return datetime.fromisoformat(_req(cells, name).replace(" ", "T"))


def parse_line(cells: Cells) -> LineInfo:
    return LineInfo(
        line_id=_req(cells, "line_id"),
        line_name=_req(cells, "line_name"),
        mode_name=_req(cells, "mode_name"),
        colour=_req(cells, "colour"),
        is_low_volume=_bool(cells, "is_low_volume"),
    )


def parse_daily(cells: Cells) -> DailyRow:
    return DailyRow(
        line_id=_req(cells, "line_id"),
        london_date=_date(cells, "london_date"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        observed_minutes=_int(cells, "observed_minutes"),
        missing_minutes=_int(cells, "missing_minutes"),
        engineering_works_minutes=_int(cells, "engineering_works_minutes"),
        service_closed_minutes=_int(cells, "service_closed_minutes"),
        scored_minutes=_int(cells, "scored_minutes"),
        good_service_minutes=_int(cells, "good_service_minutes"),
        disrupted_minutes=_int(cells, "disrupted_minutes"),
        weighted_disruption_minutes=_float(cells, "weighted_disruption_minutes"),
        incidents=_int(cells, "incidents"),
    )


def parse_weekly(cells: Cells) -> WeeklyRow:
    return WeeklyRow(
        line_id=_req(cells, "line_id"),
        week_start=_date(cells, "week_start"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        observed_minutes=_int(cells, "observed_minutes"),
        engineering_works_minutes=_int(cells, "engineering_works_minutes"),
        scored_minutes=_int(cells, "scored_minutes"),
        good_service_minutes=_int(cells, "good_service_minutes"),
        weighted_disruption_minutes=_float(cells, "weighted_disruption_minutes"),
        incidents=_int(cells, "incidents"),
        median_recovery_minutes=_opt_float(cells, "median_recovery_minutes"),
        completeness=_float(cells, "completeness"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        incidents_per_100_hours=_opt_float(cells, "incidents_per_100_hours"),
        is_week_complete=_bool(cells, "is_week_complete"),
        is_provisional=_bool(cells, "is_provisional"),
        disruption_rank=_opt_int(cells, "disruption_rank"),
        rank_movement=_opt_int(cells, "rank_movement"),
    )


def parse_incident(cells: Cells) -> IncidentRow:
    return IncidentRow(
        line_id=_req(cells, "line_id"),
        started_at_utc=_timestamp(cells, "started_at"),
        start_london_date=_date(cells, "start_london_date"),
        duration_minutes=_int(cells, "duration_minutes"),
        is_open=_bool(cells, "is_open"),
        recovery_minutes=_opt_int(cells, "recovery_minutes"),
        peak_severity_weight=_float(cells, "peak_severity_weight"),
        worst_status_description=_req(cells, "worst_status_description"),
        first_reason=_opt(cells, "first_reason"),
    )


def parse_bad_hour(cells: Cells) -> BadHourRow:
    return BadHourRow(
        line_id=_req(cells, "line_id"),
        london_hour=_int(cells, "london_hour"),
        scored_minutes=_int(cells, "scored_minutes"),
        disrupted_minutes=_int(cells, "disrupted_minutes"),
    )


# ---- Queries ---------------------------------------------------------------

DAILY_COLUMNS = (
    "line_id, london_date, scheduled_minutes, observed_minutes, missing_minutes, "
    "engineering_works_minutes, service_closed_minutes, scored_minutes, "
    "good_service_minutes, disrupted_minutes, weighted_disruption_minutes, incidents"
)
WEEKLY_COLUMNS = (
    "line_id, week_start, scheduled_minutes, observed_minutes, engineering_works_minutes, "
    "scored_minutes, good_service_minutes, weighted_disruption_minutes, incidents, "
    "median_recovery_minutes, completeness, disruption_index, good_service_pct, "
    "incidents_per_100_hours, is_week_complete, is_provisional, disruption_rank, rank_movement"
)
INCIDENT_COLUMNS = (
    "line_id, started_at, start_london_date, duration_minutes, is_open, recovery_minutes, "
    "peak_severity_weight, worst_status_description, first_reason"
)


def queries(database: str, start: date, end: date) -> dict[str, str]:
    """The SELECTs behind each recorded file, keyed by file stem."""
    window = f"date '{start.isoformat()}' and date '{end.isoformat()}'"
    return {
        "lines": (
            "select line_id, line_name, mode_name, colour, is_low_volume "
            f"from {database}.dim_lines order by line_id"
        ),
        "daily": (
            f"select {DAILY_COLUMNS} from {database}.fct_line_daily "
            f"where london_date between {window} order by line_id, london_date"
        ),
        "weekly": (
            f"select {WEEKLY_COLUMNS} from {database}.fct_line_weekly order by week_start, line_id"
        ),
        "incidents": (
            f"select {INCIDENT_COLUMNS} from {database}.int_incidents "
            f"where start_london_date between {window} order by line_id, started_at"
        ),
        # london_ts is the minute already in London time (dbt does the
        # conversion), so only the hour is read here.
        "bad_hours": (
            "select line_id, hour(london_ts) as london_hour, "
            "count_if(is_scored) as scored_minutes, "
            "count_if(is_scored and severity_weight > 0) as disrupted_minutes "
            f"from {database}.int_line_minute_status "
            f"where is_scheduled and london_date between {window} "
            "group by 1, 2 order by 1, 2"
        ),
    }


@dataclass(frozen=True)
class Recording:
    columns: list[str]
    rows: list[list[str | None]]

    def cells(self) -> list[dict[str, str | None]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


def load_recording(path: Path) -> Recording:
    data = json.loads(path.read_text())
    return Recording(columns=list(data["columns"]), rows=[list(r) for r in data["rows"]])


def dump_recording(path: Path, recording: Recording) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"columns": recording.columns, "rows": recording.rows}
    path.write_text(json.dumps(payload, indent=1) + "\n")


class RecordedMarts:
    """Query results saved as JSON (`<stem>.json` per query), filtered by window here."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _cells(self, stem: str) -> list[dict[str, str | None]]:
        return load_recording(self.directory / f"{stem}.json").cells()

    def lines(self) -> list[LineInfo]:
        return [parse_line(c) for c in self._cells("lines")]

    def daily(self, start: date, end: date) -> list[DailyRow]:
        rows = [parse_daily(c) for c in self._cells("daily")]
        return [r for r in rows if start <= r.london_date <= end]

    def weekly(self) -> list[WeeklyRow]:
        return [parse_weekly(c) for c in self._cells("weekly")]

    def incidents(self, start: date, end: date) -> list[IncidentRow]:
        rows = [parse_incident(c) for c in self._cells("incidents")]
        return [r for r in rows if start <= r.start_london_date <= end]

    def bad_hours(self, start: date, end: date) -> list[BadHourRow]:
        # Recorded already aggregated over the recording's window.
        path = self.directory / "bad_hours.json"
        return [parse_bad_hour(c) for c in self._cells("bad_hours")] if path.exists() else []


class AthenaMarts:
    """Read-only queries against a Glue database through an Athena workgroup.

    Every statement goes through `run_query`, which refuses anything but a
    single SELECT. `record_to`, when set, saves each result for replay.
    """

    def __init__(
        self,
        athena: "AthenaClient",
        *,
        workgroup: str,
        database: str,
        record_to: Path | None = None,
        max_rows: int = 50_000,
    ) -> None:
        self.athena = athena
        self.workgroup = workgroup
        self.database = database
        self.record_to = record_to
        self.max_rows = max_rows
        self.bytes_scanned = 0

    def _run(self, stem: str, start: date, end: date) -> list[dict[str, str | None]]:
        sql = queries(self.database, start, end)[stem]
        result = run_query(
            self.athena,
            sql,
            workgroup=self.workgroup,
            database=self.database,
            max_rows=self.max_rows,
        )
        if len(result.rows) >= self.max_rows:
            raise RuntimeError(f"{stem}: result hit max_rows={self.max_rows}; refusing to truncate")
        self.bytes_scanned += result.bytes_scanned
        recording = Recording(columns=result.columns, rows=result.rows)
        if self.record_to is not None:
            dump_recording(self.record_to / f"{stem}.json", recording)
        return recording.cells()

    # The window is irrelevant to lines and weekly; any dates do.
    def lines(self) -> list[LineInfo]:
        return [parse_line(c) for c in self._run("lines", date.min, date.min)]

    def daily(self, start: date, end: date) -> list[DailyRow]:
        return [parse_daily(c) for c in self._run("daily", start, end)]

    def weekly(self) -> list[WeeklyRow]:
        return [parse_weekly(c) for c in self._run("weekly", date.min, date.min)]

    def incidents(self, start: date, end: date) -> list[IncidentRow]:
        return [parse_incident(c) for c in self._run("incidents", start, end)]

    def bad_hours(self, start: date, end: date) -> list[BadHourRow]:
        return [parse_bad_hour(c) for c in self._run("bad_hours", start, end)]


def rows_by_line[T: (DailyRow, IncidentRow, WeeklyRow, BadHourRow)](
    rows: Sequence[T],
) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for row in rows:
        grouped.setdefault(row.line_id, []).append(row)
    return grouped
