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
class BuildTimes:
    """When the declared and measured weekly tables were last built.

    A failing quality gate skips fct_line_weekly_measured (and
    fct_line_measured_daily) but not fct_line_weekly, so the measured tables
    can be older than the declared ones (ADR-014, "Gate failures and the
    declared view").
    """

    declared_built_at: datetime
    measured_built_at: datetime | None
    # London date of the measured build: windows that ended before it were
    # complete in the measured tables.
    measured_built_london_date: date | None

    def measured_is_final_for(self, window_end: date) -> bool:
        """The measured tables hold the window's whole measured view.

        True when they were built with (or after) the declared tables, or
        when the window had ended before they were last built.
        """
        if self.measured_built_at is None or self.measured_built_london_date is None:
            return False
        if self.measured_built_at >= self.declared_built_at:
            return True
        return window_end < self.measured_built_london_date


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


@dataclass(frozen=True)
class SeedingRow:
    """One row of rpt_division_seeding (ADR-015)."""

    line_id: str
    seed_position: int
    division: int
    disruption_index: float | None
    good_service_pct: float | None
    unranked_reason: str | None
    seeding_start: date
    seeding_end: date
    seeding_weeks: int
    complete_weeks_available: int
    first_division_month: date
    seeding_status: str


@dataclass(frozen=True)
class DivisionMonthRow:
    """One row of rpt_divisions_monthly: a line's month in its division (D57)."""

    line_id: str
    month_start: date
    division: int
    joined_by: str
    scheduled_minutes: int
    observed_minutes: int
    disruption_index: float | None
    good_service_pct: float | None
    completeness: float
    is_provisional: bool
    unranked_reason: str | None
    division_rank: int | None
    movement: str
    next_division: int
    is_month_complete: bool
    # "provisional_month" when the month moved no one (D57); None otherwise.
    movement_held_reason: str | None = None


@dataclass(frozen=True)
class SeasonRow:
    """One row of rpt_season (D58)."""

    line_id: str
    season: str
    season_start: date
    season_end: date
    first_observed_date: date | None
    last_observed_date: date | None
    scheduled_minutes: int
    observed_minutes: int
    engineering_works_minutes: int
    incidents: int
    disruption_index: float | None
    good_service_pct: float | None
    incidents_per_100_hours: float | None
    completeness: float | None
    is_provisional: bool
    unranked_reason: str | None
    season_rank: int | None
    is_season_complete: bool


@dataclass(frozen=True)
class StrikesExcludedRow:
    """One row of rpt_league_strikes_excluded (D60)."""

    line_id: str
    week_start: date
    strike_days: int
    week_has_industrial_action: bool
    scheduled_minutes: int
    observed_minutes: int
    disruption_index: float | None
    good_service_pct: float | None
    completeness: float | None
    is_provisional: bool
    unranked_reason: str | None
    disruption_rank: int | None


@dataclass(frozen=True)
class StatusMixRow:
    """One row of rpt_weekly_status_mix: unweighted scored minutes (D61)."""

    line_id: str
    week_start: date
    status_severity: int
    is_planned: bool
    time_band: str
    scored_minutes: int


@dataclass(frozen=True)
class StationInfo:
    """One row of dim_station: a station-level NaPTAN id and what serves it."""

    naptan_id: str
    station_name: str
    lat: float
    lon: float
    line_ids: tuple[str, ...]
    modes: tuple[str, ...]
    is_measurement_station: bool


@dataclass(frozen=True)
class StationDisruptionRow:
    """One row of fct_station_disruption_weekly."""

    week_start: date
    naptan_id: str
    closure_hours: float
    step_free_hours: float
    other_disruption_hours: float
    disruptions: int
    observed_share: float


@dataclass(frozen=True)
class StationMeasuredRow:
    """One row of fct_station_weekly_measured: a station, line and direction in a week."""

    week_start: date
    naptan_id: str
    line_id: str
    direction: str
    observed_hours: float
    trains_observed: int | None
    trains_per_hour: float | None
    median_gap_minutes: float | None
    long_gaps_per_100_hours: float | None
    scheduled_trains: int | None
    trains_on_boards_share: float | None
    excess_wait_minutes: float | None
    gate_judged_hours: int
    gate_pass_share: float | None
    measured_status: str
    measured_withheld_reason: str | None


class Marts(Protocol):
    def latest_minute_london(self) -> datetime | None: ...
    def lines(self) -> list[LineInfo]: ...
    def daily(self, start: date, end: date) -> list[DailyRow]: ...
    def weekly(self) -> list[WeeklyRow]: ...
    def incidents(self, start: date, end: date) -> list[IncidentRow]: ...
    def bad_hours(self, start: date, end: date) -> list[BadHourRow]: ...
    def monday_morning(self, start: date, end: date) -> list[MondayMorningRow]: ...
    def measured_daily(self, start: date, end: date) -> list[MeasuredDailyRow]: ...
    def build_times(self) -> BuildTimes | None: ...
    def division_seeding(self) -> list[SeedingRow]: ...
    def divisions(self) -> list[DivisionMonthRow]: ...
    def seasons(self) -> list[SeasonRow]: ...
    def strikes_excluded(self) -> list[StrikesExcludedRow]: ...
    def status_mix(self) -> list[StatusMixRow]: ...
    def stations(self) -> list[StationInfo]: ...
    def station_disruption_weekly(self) -> list[StationDisruptionRow]: ...
    def station_measured_weekly(self) -> list[StationMeasuredRow]: ...


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


def parse_build_times(cells: Cells) -> BuildTimes | None:
    declared = _opt_timestamp(cells, "declared_built_at")
    if declared is None:
        return None
    return BuildTimes(
        declared_built_at=declared,
        measured_built_at=_opt_timestamp(cells, "measured_built_at"),
        measured_built_london_date=_opt_date(cells, "measured_built_london_date"),
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


def _opt_date(cells: Cells, name: str) -> date | None:
    return None if _opt(cells, name) is None else _date(cells, name)


def parse_seeding(cells: Cells) -> SeedingRow:
    return SeedingRow(
        line_id=_req(cells, "line_id"),
        seed_position=_int(cells, "seed_position"),
        division=_int(cells, "division"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        unranked_reason=_opt(cells, "unranked_reason"),
        seeding_start=_date(cells, "seeding_start"),
        seeding_end=_date(cells, "seeding_end"),
        seeding_weeks=_int(cells, "seeding_weeks"),
        complete_weeks_available=_int(cells, "complete_weeks_available"),
        first_division_month=_date(cells, "first_division_month"),
        seeding_status=_req(cells, "seeding_status"),
    )


def parse_division_month(cells: Cells) -> DivisionMonthRow:
    return DivisionMonthRow(
        line_id=_req(cells, "line_id"),
        month_start=_date(cells, "month_start"),
        division=_int(cells, "division"),
        joined_by=_req(cells, "joined_by"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        observed_minutes=_int(cells, "observed_minutes"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        completeness=_float(cells, "completeness"),
        is_provisional=_bool(cells, "is_provisional"),
        unranked_reason=_opt(cells, "unranked_reason"),
        division_rank=_opt_int(cells, "division_rank"),
        movement=_req(cells, "movement"),
        next_division=_int(cells, "next_division"),
        is_month_complete=_bool(cells, "is_month_complete"),
        movement_held_reason=_opt(cells, "movement_held_reason"),
    )


def parse_season(cells: Cells) -> SeasonRow:
    return SeasonRow(
        line_id=_req(cells, "line_id"),
        season=_req(cells, "season"),
        season_start=_date(cells, "season_start"),
        season_end=_date(cells, "season_end"),
        first_observed_date=_opt_date(cells, "first_observed_date"),
        last_observed_date=_opt_date(cells, "last_observed_date"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        observed_minutes=_int(cells, "observed_minutes"),
        engineering_works_minutes=_int(cells, "engineering_works_minutes"),
        incidents=_int(cells, "incidents"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        incidents_per_100_hours=_opt_float(cells, "incidents_per_100_hours"),
        completeness=_opt_float(cells, "completeness"),
        is_provisional=_bool(cells, "is_provisional"),
        unranked_reason=_opt(cells, "unranked_reason"),
        season_rank=_opt_int(cells, "season_rank"),
        is_season_complete=_bool(cells, "is_season_complete"),
    )


def parse_strikes_excluded(cells: Cells) -> StrikesExcludedRow:
    return StrikesExcludedRow(
        line_id=_req(cells, "line_id"),
        week_start=_date(cells, "week_start"),
        strike_days=_int(cells, "strike_days"),
        week_has_industrial_action=_bool(cells, "week_has_industrial_action"),
        scheduled_minutes=_int(cells, "scheduled_minutes"),
        observed_minutes=_int(cells, "observed_minutes"),
        disruption_index=_opt_float(cells, "disruption_index"),
        good_service_pct=_opt_float(cells, "good_service_pct"),
        completeness=_opt_float(cells, "completeness"),
        is_provisional=_bool(cells, "is_provisional"),
        unranked_reason=_opt(cells, "unranked_reason"),
        disruption_rank=_opt_int(cells, "disruption_rank"),
    )


def parse_status_mix(cells: Cells) -> StatusMixRow:
    return StatusMixRow(
        line_id=_req(cells, "line_id"),
        week_start=_date(cells, "week_start"),
        status_severity=_int(cells, "status_severity"),
        is_planned=_bool(cells, "is_planned"),
        time_band=_req(cells, "time_band"),
        scored_minutes=_int(cells, "scored_minutes"),
    )


def _list(cells: Cells, name: str) -> tuple[str, ...]:
    # Athena prints array(varchar) as "[a, b]".
    text = _req(cells, name).strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ValueError(f"column {name} is not an array: {text!r}")
    return tuple(item.strip() for item in text[1:-1].split(",") if item.strip())


def parse_station(cells: Cells) -> StationInfo:
    return StationInfo(
        naptan_id=_req(cells, "naptan_id"),
        station_name=_req(cells, "station_name"),
        lat=_float(cells, "lat"),
        lon=_float(cells, "lon"),
        line_ids=_list(cells, "line_ids"),
        modes=_list(cells, "modes"),
        is_measurement_station=_bool(cells, "is_measurement_station"),
    )


def parse_station_disruption(cells: Cells) -> StationDisruptionRow:
    return StationDisruptionRow(
        week_start=_date(cells, "week_start"),
        naptan_id=_req(cells, "naptan_id"),
        closure_hours=_float(cells, "closure_hours"),
        step_free_hours=_float(cells, "step_free_hours"),
        other_disruption_hours=_float(cells, "other_disruption_hours"),
        disruptions=_int(cells, "disruptions"),
        observed_share=_float(cells, "observed_share"),
    )


def parse_station_measured(cells: Cells) -> StationMeasuredRow:
    return StationMeasuredRow(
        week_start=_date(cells, "week_start"),
        naptan_id=_req(cells, "naptan_id"),
        line_id=_req(cells, "line_id"),
        direction=_req(cells, "direction"),
        observed_hours=_float(cells, "observed_hours"),
        trains_observed=_opt_int(cells, "trains_observed"),
        trains_per_hour=_opt_float(cells, "trains_per_hour"),
        median_gap_minutes=_opt_float(cells, "median_gap_minutes"),
        long_gaps_per_100_hours=_opt_float(cells, "long_gaps_per_100_hours"),
        scheduled_trains=_opt_int(cells, "scheduled_trains"),
        trains_on_boards_share=_opt_float(cells, "trains_on_boards_share"),
        excess_wait_minutes=_opt_float(cells, "excess_wait_minutes"),
        gate_judged_hours=_int(cells, "gate_judged_hours"),
        gate_pass_share=_opt_float(cells, "gate_pass_share"),
        measured_status=_req(cells, "measured_status"),
        measured_withheld_reason=_opt(cells, "measured_withheld_reason"),
    )


# ---- Queries ---------------------------------------------------------------

DAILY_COLUMNS = (
    "line_id, london_date, scheduled_minutes, observed_minutes, missing_minutes, "
    "engineering_works_minutes, service_closed_minutes, scored_minutes, "
    "good_service_minutes, disrupted_minutes, weighted_disruption_minutes, "
    "time_weighted_scored_minutes, incidents"
)
DECLARED_WEEKLY_COLUMNS = (
    "line_id",
    "week_start",
    "scheduled_minutes",
    "observed_minutes",
    "engineering_works_minutes",
    "scored_minutes",
    "good_service_minutes",
    "weighted_disruption_minutes",
    "time_weighted_scored_minutes",
    "incidents",
    "median_recovery_minutes",
    "completeness",
    "disruption_index",
    "good_service_pct",
    "incidents_per_100_hours",
    "is_week_complete",
    "is_provisional",
    "unranked_reason",
    "disruption_rank",
    "rank_movement",
)
MEASURED_WEEKLY_COLUMNS = (*MEASURED_SUM_FIELDS, "measured_withheld_reason", "experienced_rank")
# fct_line_weekly holds the declared view and fct_line_weekly_measured the
# measured one (ADR-014); the export reads them joined, with the old names.
WEEKLY_COLUMNS = ", ".join(
    [f"declared.{c}" for c in DECLARED_WEEKLY_COLUMNS]
    + [f"measured.{c}" for c in MEASURED_WEEKLY_COLUMNS]
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

SEEDING_COLUMNS = (
    "line_id, seed_position, division, disruption_index, good_service_pct, unranked_reason, "
    "seeding_start, seeding_end, seeding_weeks, complete_weeks_available, "
    "first_division_month, seeding_status"
)
DIVISION_COLUMNS = (
    "line_id, month_start, division, joined_by, scheduled_minutes, observed_minutes, "
    "disruption_index, good_service_pct, completeness, is_provisional, unranked_reason, "
    "division_rank, movement, next_division, is_month_complete, movement_held_reason"
)
SEASON_COLUMNS = (
    "line_id, season, season_start, season_end, first_observed_date, last_observed_date, "
    "scheduled_minutes, observed_minutes, engineering_works_minutes, incidents, "
    "disruption_index, good_service_pct, incidents_per_100_hours, completeness, "
    "is_provisional, unranked_reason, season_rank, is_season_complete"
)
STRIKES_EXCLUDED_COLUMNS = (
    "line_id, week_start, strike_days, week_has_industrial_action, scheduled_minutes, "
    "observed_minutes, disruption_index, good_service_pct, completeness, is_provisional, "
    "unranked_reason, disruption_rank"
)
STATUS_MIX_COLUMNS = "line_id, week_start, status_severity, is_planned, time_band, scored_minutes"


# Station files carry a bounded history: about 430 stations with two or more
# line-directions each would pass the 50,000-row guard after roughly 30 weeks.
STATION_HISTORY_WEEKS = 12
STATION_HISTORY_FILTER = f"week_start >= date_add('week', -{STATION_HISTORY_WEEKS}, current_date)"
STATION_COLUMNS = "naptan_id, station_name, lat, lon, line_ids, modes, is_measurement_station"
STATION_DISRUPTION_COLUMNS = (
    "week_start, naptan_id, closure_hours, step_free_hours, other_disruption_hours, "
    "disruptions, observed_share"
)
STATION_MEASURED_COLUMNS = (
    "week_start, naptan_id, line_id, direction, observed_hours, trains_observed, "
    "trains_per_hour, median_gap_minutes, long_gaps_per_100_hours, scheduled_trains, "
    "trains_on_boards_share, excess_wait_minutes, gate_judged_hours, gate_pass_share, "
    "measured_status, measured_withheld_reason"
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
            f"select {WEEKLY_COLUMNS} from {database}.fct_line_weekly as declared "
            f"left join {database}.fct_line_weekly_measured as measured "
            "on declared.line_id = measured.line_id and declared.week_start = measured.week_start "
            "order by declared.week_start, declared.line_id"
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
        # When each weekly table was last built (ADR-014, D55).
        "build_times": (
            f"select (select max(built_at) from {database}.fct_line_weekly) as declared_built_at, "
            f"(select max(built_at) from {database}.fct_line_weekly_measured) "
            "as measured_built_at, "
            f"(select max(built_london_date) from {database}.fct_line_weekly_measured) "
            "as measured_built_london_date"
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
        # The M6 tables below are small (a row per line and month, season
        # or week), so each is read whole.
        "division_seeding": (
            f"select {SEEDING_COLUMNS} from {database}.rpt_division_seeding order by seed_position"
        ),
        "divisions": (
            f"select {DIVISION_COLUMNS} from {database}.rpt_divisions_monthly "
            "order by month_start, division, line_id"
        ),
        "seasons": (
            f"select {SEASON_COLUMNS} from {database}.rpt_season order by season_start, line_id"
        ),
        "strikes_excluded": (
            f"select {STRIKES_EXCLUDED_COLUMNS} from {database}.rpt_league_strikes_excluded "
            "order by week_start, line_id"
        ),
        "status_mix": (
            f"select {STATUS_MIX_COLUMNS} from {database}.rpt_weekly_status_mix "
            "order by week_start, line_id, status_severity, is_planned, time_band"
        ),
        "stations": (f"select {STATION_COLUMNS} from {database}.dim_station order by naptan_id"),
        "station_disruption": (
            f"select {STATION_DISRUPTION_COLUMNS} "
            f"from {database}.fct_station_disruption_weekly "
            f"where {STATION_HISTORY_FILTER} order by week_start, naptan_id"
        ),
        "station_measured": (
            f"select {STATION_MEASURED_COLUMNS} "
            f"from {database}.fct_station_weekly_measured "
            f"where {STATION_HISTORY_FILTER} "
            "order by week_start, naptan_id, line_id, direction"
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

    def build_times(self) -> BuildTimes | None:
        # Recordings made before the measured view had its own table have
        # no build times; their measured columns came from one build.
        cells = self._optional("build_times")
        return parse_build_times(cells[0]) if cells else None

    def _optional(self, stem: str) -> list[dict[str, str | None]]:
        # Recordings made before a mart existed have no file for it.
        path = self.directory / f"{stem}.json"
        return self._cells(stem) if path.exists() else []

    def division_seeding(self) -> list[SeedingRow]:
        return [parse_seeding(c) for c in self._optional("division_seeding")]

    def divisions(self) -> list[DivisionMonthRow]:
        return [parse_division_month(c) for c in self._optional("divisions")]

    def seasons(self) -> list[SeasonRow]:
        return [parse_season(c) for c in self._optional("seasons")]

    def strikes_excluded(self) -> list[StrikesExcludedRow]:
        return [parse_strikes_excluded(c) for c in self._optional("strikes_excluded")]

    def status_mix(self) -> list[StatusMixRow]:
        return [parse_status_mix(c) for c in self._optional("status_mix")]

    # The station marts are empty before the first line_arrivals data, and a
    # recording made before M8 has no files for them.
    def stations(self) -> list[StationInfo]:
        return [parse_station(c) for c in self._optional("stations")]

    def station_disruption_weekly(self) -> list[StationDisruptionRow]:
        return [parse_station_disruption(c) for c in self._optional("station_disruption")]

    def station_measured_weekly(self) -> list[StationMeasuredRow]:
        return [parse_station_measured(c) for c in self._optional("station_measured")]


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

    def build_times(self) -> BuildTimes | None:
        cells = self._run("build_times", date.min, date.min)
        return parse_build_times(cells[0]) if cells else None

    def division_seeding(self) -> list[SeedingRow]:
        return [parse_seeding(c) for c in self._run("division_seeding", date.min, date.min)]

    def divisions(self) -> list[DivisionMonthRow]:
        return [parse_division_month(c) for c in self._run("divisions", date.min, date.min)]

    def seasons(self) -> list[SeasonRow]:
        return [parse_season(c) for c in self._run("seasons", date.min, date.min)]

    def strikes_excluded(self) -> list[StrikesExcludedRow]:
        rows = self._run("strikes_excluded", date.min, date.min)
        return [parse_strikes_excluded(c) for c in rows]

    def status_mix(self) -> list[StatusMixRow]:
        return [parse_status_mix(c) for c in self._run("status_mix", date.min, date.min)]

    def stations(self) -> list[StationInfo]:
        return [parse_station(c) for c in self._run("stations", date.min, date.min)]

    def station_disruption_weekly(self) -> list[StationDisruptionRow]:
        rows = self._run("station_disruption", date.min, date.min)
        return [parse_station_disruption(c) for c in rows]

    def station_measured_weekly(self) -> list[StationMeasuredRow]:
        rows = self._run("station_measured", date.min, date.min)
        return [parse_station_measured(c) for c in rows]


def rows_by_line[
    T: (DailyRow, IncidentRow, WeeklyRow, BadHourRow, MondayMorningRow, MeasuredDailyRow)
](
    rows: Sequence[T],
) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for row in rows:
        grouped.setdefault(row.line_id, []).append(row)
    return grouped
