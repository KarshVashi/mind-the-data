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
    # seed_measured_lines scores the line in the measured view (D43).
    is_measured: bool = False


@dataclass(frozen=True)
class MeasuredSums:
    """Additive measured-view counts (fct_line_measured_daily), for a day or any window."""

    scheduled_measurement_hours: int = 0
    observed_measurement_hours: int = 0
    observed_trains: int = 0
    observed_hours_scheduled_trains: int = 0
    measured_hours: int = 0
    excess_wait_hours: int = 0
    sum_excess_wait_seconds: float = 0.0
    long_gaps: int = 0
    fallback_measured_hours: int = 0
    gate_judged_hours: int = 0
    gate_in_band_hours: int = 0
    good_service_measured_hours: int = 0
    honesty_gap_hours: int = 0

    def __add__(self, other: "MeasuredSums") -> "MeasuredSums":
        return MeasuredSums(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in self.__dataclass_fields__
            }
        )


MEASURED_SUM_FIELDS = tuple(MeasuredSums.__dataclass_fields__)


@dataclass(frozen=True)
class MeasuredDailyRow:
    """One row of fct_line_measured_daily: a measured line's sums for a London date."""

    line_id: str
    london_date: date
    sums: MeasuredSums


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
    # Scored minutes at their time weight: the Disruption Index's denominator (D19).
    time_weighted_scored_minutes: float
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
    time_weighted_scored_minutes: float
    incidents: int
    median_recovery_minutes: float | None
    completeness: float
    disruption_index: float | None
    good_service_pct: float | None
    incidents_per_100_hours: float | None
    is_week_complete: bool
    is_provisional: bool
    # "no_scored_minutes" or "engineering_works" (D22); None when ranked.
    unranked_reason: str | None
    disruption_rank: int | None
    rank_movement: int | None
    # The measured view as dbt summed and judged it (D42, D51 to D53); None
    # for a line seed_measured_lines leaves out, or a recording without it.
    measured_sums: MeasuredSums | None = None
    measured_withheld_reason: str | None = None
    experienced_rank: int | None = None


@dataclass(frozen=True)
class IncidentRow:
    line_id: str
    started_at_utc: datetime
    # London wall-clock time of the first minute, as dbt computed it.
    started_at_london: datetime
    start_london_date: date
    duration_minutes: int
    is_open: bool
    recovery_minutes: int | None
    peak_severity_weight: float
    # Each incident minute at its severity weight; orders the worst incident (D48).
    severity_weighted_minutes: float
    worst_status_description: str
    first_reason: str | None


@dataclass(frozen=True)
class BadHourRow:
    """Share of a line's scored minutes that were disrupted, per London hour of day."""

    line_id: str
    london_hour: int
    scored_minutes: int
    disrupted_minutes: int


@dataclass(frozen=True)
class MondayMorningRow:
    """One row of rpt_monday_morning: a line's Monday AM peak (D49)."""

    line_id: str
    week_start: date
    scheduled_minutes: int
    scored_minutes: int
    completeness: float
    engineering_works_share: float
    monday_morning_index: float | None
    good_service_pct: float | None
    is_provisional: bool
    unranked_reason: str | None
    monday_morning_rank: int | None


class Marts(Protocol):
    def latest_minute_london(self) -> datetime | None: ...
    def lines(self) -> list[LineInfo]: ...
    def daily(self, start: date, end: date) -> list[DailyRow]: ...
    def weekly(self) -> list[WeeklyRow]: ...
    def incidents(self, start: date, end: date) -> list[IncidentRow]: ...
    def bad_hours(self, start: date, end: date) -> list[BadHourRow]: ...
    def monday_morning(self, start: date, end: date) -> list[MondayMorningRow]: ...
    def measured_daily(self, start: date, end: date) -> list[MeasuredDailyRow]: ...


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


def _opt_timestamp(cells: Cells, name: str) -> datetime | None:
    return None if _opt(cells, name) is None else _timestamp(cells, name)


def parse_line(cells: Cells) -> LineInfo:
    return LineInfo(
        line_id=_req(cells, "line_id"),
        line_name=_req(cells, "line_name"),
        mode_name=_req(cells, "mode_name"),
        colour=_req(cells, "colour"),
        is_low_volume=_bool(cells, "is_low_volume"),
        is_measured=_opt(cells, "is_measured") == "true",
    )


def _measured_sums(cells: Cells) -> MeasuredSums:
    values: dict[str, int | float] = {}
    for name in MEASURED_SUM_FIELDS:
        if name == "sum_excess_wait_seconds":
            values[name] = _float(cells, name)
        else:
            values[name] = _int(cells, name)
    return MeasuredSums(**values)  # type: ignore[arg-type]


def parse_measured_daily(cells: Cells) -> MeasuredDailyRow:
    return MeasuredDailyRow(
        line_id=_req(cells, "line_id"),
        london_date=_date(cells, "london_date"),
        sums=_measured_sums(cells),
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
        time_weighted_scored_minutes=_float(cells, "time_weighted_scored_minutes"),
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
        time_weighted_scored_minutes=_float(cells, "time_weighted_scored_minutes"),
        incidents=_int(cells, "incidents"),
        median_recovery_minutes=_opt_float(cells, "median_recovery_minutes"),
        completeness=_float(cells, "completeness"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        incidents_per_100_hours=_opt_float(cells, "incidents_per_100_hours"),
        is_week_complete=_bool(cells, "is_week_complete"),
        is_provisional=_bool(cells, "is_provisional"),
        unranked_reason=_opt(cells, "unranked_reason"),
        disruption_rank=_opt_int(cells, "disruption_rank"),
        rank_movement=_opt_int(cells, "rank_movement"),
        # A not-measured line, and a recording made before the measured
        # columns, have no sums.
        measured_sums=None if _opt(cells, "measured_hours") is None else _measured_sums(cells),
        measured_withheld_reason=_opt(cells, "measured_withheld_reason"),
        experienced_rank=_opt_int(cells, "experienced_rank"),
    )


def parse_incident(cells: Cells) -> IncidentRow:
    return IncidentRow(
        line_id=_req(cells, "line_id"),
        started_at_utc=_timestamp(cells, "started_at"),
        started_at_london=_timestamp(cells, "started_at_london"),
        start_london_date=_date(cells, "start_london_date"),
        duration_minutes=_int(cells, "duration_minutes"),
        is_open=_bool(cells, "is_open"),
        recovery_minutes=_opt_int(cells, "recovery_minutes"),
        peak_severity_weight=_float(cells, "peak_severity_weight"),
        severity_weighted_minutes=_float(cells, "severity_weighted_minutes"),
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


def parse_monday_morning(cells: Cells) -> MondayMorningRow:
    return MondayMorningRow(
        line_id=_req(cells, "line_id"),
        week_start=_date(cells, "week_start"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        scored_minutes=_int(cells, "scored_minutes"),
        completeness=_float(cells, "completeness"),
        engineering_works_share=_float(cells, "engineering_works_share"),
        monday_morning_index=_opt_float(cells, "monday_morning_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        is_provisional=_bool(cells, "is_provisional"),
        unranked_reason=_opt(cells, "unranked_reason"),
        monday_morning_rank=_opt_int(cells, "monday_morning_rank"),
    )


# ---- Queries ---------------------------------------------------------------

DAILY_COLUMNS = (
    "line_id, london_date, scheduled_minutes, observed_minutes, missing_minutes, "
    "engineering_works_minutes, service_closed_minutes, scored_minutes, "
    "good_service_minutes, disrupted_minutes, weighted_disruption_minutes, "
    "time_weighted_scored_minutes, incidents"
)
WEEKLY_COLUMNS = (
    "line_id, week_start, scheduled_minutes, observed_minutes, engineering_works_minutes, "
    "scored_minutes, good_service_minutes, weighted_disruption_minutes, "
    "time_weighted_scored_minutes, incidents, median_recovery_minutes, completeness, "
    "disruption_index, good_service_pct, incidents_per_100_hours, is_week_complete, "
    "is_provisional, unranked_reason, disruption_rank, rank_movement, "
    + ", ".join(MEASURED_SUM_FIELDS)
    + ", measured_withheld_reason, experienced_rank"
)
MEASURED_DAILY_COLUMNS = "line_id, london_date, " + ", ".join(MEASURED_SUM_FIELDS)
INCIDENT_COLUMNS = (
    "incidents.line_id, incidents.started_at, minutes.london_ts as started_at_london, "
    "incidents.start_london_date, incidents.duration_minutes, incidents.is_open, "
    "incidents.recovery_minutes, incidents.peak_severity_weight, "
    "incidents.severity_weighted_minutes, incidents.worst_status_description, "
    "incidents.first_reason"
)
MONDAY_MORNING_COLUMNS = (
    "line_id, week_start, scheduled_minutes, scored_minutes, completeness, "
    "engineering_works_share, monday_morning_index, good_service_pct, is_provisional, "
    "unranked_reason, monday_morning_rank"
)


def queries(database: str, start: date, end: date) -> dict[str, str]:
    """The SELECTs behind each recorded file, keyed by file stem."""
    window = f"date '{start.isoformat()}' and date '{end.isoformat()}'"
    return {
        "lines": (
            "select lines.line_id, lines.line_name, lines.mode_name, lines.colour, "
            "lines.is_low_volume, coalesce(measured.is_measured, false) as is_measured "
            f"from {database}.dim_lines as lines "
            f"left join {database}.seed_measured_lines as measured "
            "on lines.line_id = measured.line_id order by lines.line_id"
        ),
        "daily": (
            f"select {DAILY_COLUMNS} from {database}.fct_line_daily "
            f"where london_date between {window} order by line_id, london_date"
        ),
        "weekly": (
            f"select {WEEKLY_COLUMNS} from {database}.fct_line_weekly order by week_start, line_id"
        ),
        # London time comes from dbt (int_line_minute_status.london_ts), not
        # from a conversion here.
        "incidents": (
            f"select {INCIDENT_COLUMNS} from {database}.int_incidents as incidents "
            f"inner join {database}.int_line_minute_status as minutes "
            "on incidents.line_id = minutes.line_id "
            "and incidents.started_at = minutes.minute_utc "
            f"where incidents.start_london_date between {window} "
            "order by incidents.line_id, incidents.started_at"
        ),
        # The newest minute built, in London time: how far the data reaches.
        "freshness": (
            f"select max(london_ts) as latest_london_ts from {database}.int_line_minute_status"
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
        # week_start is the Monday itself, so the window picks the Mondays in it.
        "monday_morning": (
            f"select {MONDAY_MORNING_COLUMNS} from {database}.rpt_monday_morning "
            f"where week_start between {window} order by week_start, line_id"
        ),
        "measured_daily": (
            f"select {MEASURED_DAILY_COLUMNS} from {database}.fct_line_measured_daily "
            f"where london_date between {window} order by line_id, london_date"
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

    def latest_minute_london(self) -> datetime | None:
        cells = self._cells("freshness")
        return _opt_timestamp(cells[0], "latest_london_ts") if cells else None

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

    def monday_morning(self, start: date, end: date) -> list[MondayMorningRow]:
        path = self.directory / "monday_morning.json"
        if not path.exists():
            return []
        rows = [parse_monday_morning(c) for c in self._cells("monday_morning")]
        return [r for r in rows if start <= r.week_start <= end]

    def measured_daily(self, start: date, end: date) -> list[MeasuredDailyRow]:
        path = self.directory / "measured_daily.json"
        if not path.exists():
            return []
        rows = [parse_measured_daily(c) for c in self._cells("measured_daily")]
        return [r for r in rows if start <= r.london_date <= end]


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

    # The window is irrelevant to lines, weekly and freshness; any dates do.
    def latest_minute_london(self) -> datetime | None:
        cells = self._run("freshness", date.min, date.min)
        return _opt_timestamp(cells[0], "latest_london_ts") if cells else None

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

    def monday_morning(self, start: date, end: date) -> list[MondayMorningRow]:
        return [parse_monday_morning(c) for c in self._run("monday_morning", start, end)]

    def measured_daily(self, start: date, end: date) -> list[MeasuredDailyRow]:
        return [parse_measured_daily(c) for c in self._run("measured_daily", start, end)]


def rows_by_line[
    T: (DailyRow, IncidentRow, WeeklyRow, BadHourRow, MondayMorningRow, MeasuredDailyRow)
](
    rows: Sequence[T],
) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for row in rows:
        grouped.setdefault(row.line_id, []).append(row)
    return grouped
