"""Typed views of the TfL responses the collector stores.

Raw objects keep TfL's payload unchanged; these models are the reading side.
They are deliberately lenient: unknown fields are ignored, and only the fields
the metrics depend on are required, so a TfL schema addition never stops a
poll from being understood. A required field going missing, or a timestamp
arriving without a zone, is a real contract change and fails validation.

Datetimes are always aware UTC. TfL fills unset dates with the .NET minimum
value ``0001-01-01T00:00:00`` (no zone); optional datetime fields read that as
None rather than as a naive date.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from pydantic.alias_generators import to_camel

from mind_the_data.common.clock import ensure_utc


def _sentinel_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("0001-01-01"):
        return None
    if isinstance(value, datetime) and value.year == 1:
        return None
    return value


def _aware_utc(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)


def _aware_utc_required(value: datetime) -> datetime:
    return ensure_utc(value)


# Naive values are rejected: guessing a zone would silently shift metrics.
UtcDatetime = Annotated[datetime, AfterValidator(_aware_utc_required)]
OptionalUtcDatetime = Annotated[
    datetime | None, BeforeValidator(_sentinel_to_none), AfterValidator(_aware_utc)
]


class TflModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


# ---- Line status and disruption -----------------------------------------------


class ValidityPeriod(TflModel):
    from_date: UtcDatetime
    to_date: UtcDatetime
    is_now: bool = False


class AffectedRoute(TflModel):
    id: str | None = None
    line_id: str | None = None
    name: str | None = None
    direction: str | None = None


class AffectedStop(TflModel):
    id: str | None = None
    naptan_id: str | None = None
    common_name: str | None = None


class Disruption(TflModel):
    """One entry of ``Line/Mode/{modes}/Disruption`` or a status's disruption."""

    category: str
    description: str
    type: str | None = None
    category_description: str | None = None
    summary: str | None = None
    additional_info: str | None = None
    closure_text: str | None = None
    created: OptionalUtcDatetime = None
    last_update: OptionalUtcDatetime = None
    affected_routes: list[AffectedRoute] = Field(default_factory=list)
    affected_stops: list[AffectedStop] = Field(default_factory=list)


class LineStatusEntry(TflModel):
    """One status of a line. A line can carry several at once."""

    status_severity: int
    status_severity_description: str
    line_id: str | None = None
    reason: str | None = None
    validity_periods: list[ValidityPeriod] = Field(default_factory=list)
    disruption: Disruption | None = None


class LineStatus(TflModel):
    """One line from ``Line/Mode/{modes}/Status``."""

    id: str
    mode_name: str
    line_statuses: list[LineStatusEntry]
    name: str | None = None
    created: OptionalUtcDatetime = None
    modified: OptionalUtcDatetime = None


# ---- Stop disruption ----------------------------------------------------------


class ConcernedLine(TflModel):
    id: str
    direction: str | None = None


class StopDisruption(TflModel):
    """One entry of ``StopPoint/Mode/{modes}/Disruption``."""

    atco_code: str
    from_date: UtcDatetime
    to_date: UtcDatetime
    type: str | None = None
    mode: str | None = None
    description: str | None = None
    common_name: str | None = None
    station_atco_code: str | None = None
    appearance: str | None = None
    additional_information: str | None = None
    concerned_lines: list[ConcernedLine] = Field(default_factory=list)


# ---- Arrivals -----------------------------------------------------------------


class Arrival(TflModel):
    """One prediction from ``StopPoint/{id}/Arrivals``."""

    id: str
    vehicle_id: str
    naptan_id: str
    line_id: str
    time_to_station: int
    expected_arrival: UtcDatetime
    timestamp: UtcDatetime
    station_name: str | None = None
    platform_name: str | None = None
    direction: str | None = None
    destination_naptan_id: str | None = None
    destination_name: str | None = None
    mode_name: str | None = None
    current_location: str | None = None
    towards: str | None = None
    time_to_live: OptionalUtcDatetime = None


# ---- Route sequence -----------------------------------------------------------


class MatchedStop(TflModel):
    id: str
    name: str
    station_id: str | None = None
    parent_id: str | None = None
    top_most_parent_id: str | None = None
    ics_id: str | None = None
    zone: str | None = None
    modes: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None


class StopPointSequence(TflModel):
    line_id: str
    direction: str
    branch_id: int
    stop_point: list[MatchedStop]
    next_branch_ids: list[int] = Field(default_factory=list)
    prev_branch_ids: list[int] = Field(default_factory=list)
    service_type: str | None = None


class OrderedLineRoute(TflModel):
    name: str
    naptan_ids: list[str]
    service_type: str | None = None


class RouteSequence(TflModel):
    """``Line/{id}/Route/Sequence/{direction}``."""

    line_id: str
    direction: str
    ordered_line_routes: list[OrderedLineRoute]
    line_name: str | None = None
    mode: str | None = None
    is_outbound_only: bool | None = None
    stations: list[MatchedStop] = Field(default_factory=list)
    stop_point_sequences: list[StopPointSequence] = Field(default_factory=list)


# ---- Timetable ----------------------------------------------------------------
# Timetable times are London service-day clock times without a date, and hours
# run past 23 for trains after midnight (TfL sends "27" for 03:xx the next
# morning). They stay as plain numbers here; dbt turns them into timestamps.


class ClockTime(TflModel):
    # The latest Night Tube journeys sit around hour 27; 30 leaves headroom.
    hour: int = Field(ge=0, le=30)
    minute: int = Field(ge=0, le=59)


class KnownJourney(ClockTime):
    interval_id: int


class ServiceFrequency(TflModel):
    lowest_frequency: float | None = None
    highest_frequency: float | None = None


class Period(TflModel):
    type: str
    from_time: ClockTime
    to_time: ClockTime
    frequency: ServiceFrequency | None = None


class Schedule(TflModel):
    name: str
    known_journeys: list[KnownJourney]
    first_journey: KnownJourney | None = None
    last_journey: KnownJourney | None = None
    periods: list[Period] = Field(default_factory=list)


class Interval(TflModel):
    stop_id: str
    time_to_arrival: float


class StationInterval(TflModel):
    id: str
    intervals: list[Interval]


class TimetableRoute(TflModel):
    station_intervals: list[StationInterval]
    schedules: list[Schedule]


class TimetableBody(TflModel):
    departure_stop_id: str
    routes: list[TimetableRoute]


class DisambiguationOption(TflModel):
    description: str | None = None
    uri: str


class Disambiguation(TflModel):
    disambiguation_options: list[DisambiguationOption] = Field(default_factory=list)


class Timetable(TflModel):
    """``Line/{id}/Timetable/{stopPointId}``.

    Without ``?direction=`` TfL answers with a disambiguation and no timetable,
    so ``timetable`` is None in that case.
    """

    line_id: str | None = None
    line_name: str | None = None
    direction: str | None = None
    timetable: TimetableBody | None = None
    disambiguation: Disambiguation | None = None
    stations: list[MatchedStop] = Field(default_factory=list)
    stops: list[MatchedStop] = Field(default_factory=list)

    @model_validator(mode="after")
    def _timetable_or_disambiguation(self) -> Self:
        # An error body or {} would otherwise read as an empty timetable,
        # turning a failed fetch into "no service" rather than missing data.
        if (self.timetable is None) == (self.disambiguation is None):
            raise ValueError("expected exactly one of timetable or disambiguation")
        return self


# ---- Severity -----------------------------------------------------------------


class Severity(TflModel):
    """One entry of ``Line/Meta/Severity``. Codes are per mode, not global."""

    mode_name: str
    severity_level: int
    description: str


# ---- Parse helpers ------------------------------------------------------------

_LINE_STATUSES = TypeAdapter(list[LineStatus])
_DISRUPTIONS = TypeAdapter(list[Disruption])
_STOP_DISRUPTIONS = TypeAdapter(list[StopDisruption])
_ARRIVALS = TypeAdapter(list[Arrival])
_SEVERITIES = TypeAdapter(list[Severity])


def parse_line_statuses(payload: Any) -> list[LineStatus]:
    return _LINE_STATUSES.validate_python(payload)


def parse_line_disruptions(payload: Any) -> list[Disruption]:
    return _DISRUPTIONS.validate_python(payload)


def parse_stop_disruptions(payload: Any) -> list[StopDisruption]:
    return _STOP_DISRUPTIONS.validate_python(payload)


def parse_arrivals(payload: Any) -> list[Arrival]:
    return _ARRIVALS.validate_python(payload)


def parse_route_sequence(payload: Any) -> RouteSequence:
    return RouteSequence.model_validate(payload)


def parse_timetable(payload: Any) -> Timetable:
    return Timetable.model_validate(payload)


def parse_severities(payload: Any) -> list[Severity]:
    return _SEVERITIES.validate_python(payload)


def severity_lookup(severities: Sequence[Severity]) -> dict[tuple[str, int], str]:
    """Map (mode, level) to description; the same level differs between modes.

    Raises ValueError if one (mode, level) pair has two different descriptions,
    since picking either would silently relabel statuses.
    """
    lookup: dict[tuple[str, int], str] = {}
    for s in severities:
        key = (s.mode_name, s.severity_level)
        if lookup.get(key, s.description) != s.description:
            raise ValueError(f"conflicting descriptions for severity {key}")
        lookup[key] = s.description
    return lookup
