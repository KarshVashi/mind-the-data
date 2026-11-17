import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mind_the_data.ingest.models import (
    Arrival,
    LineStatus,
    RouteSequence,
    Severity,
    StopDisruption,
    parse_arrivals,
    parse_line_disruptions,
    parse_line_statuses,
    parse_route_sequence,
    parse_severities,
    parse_stop_disruptions,
    parse_timetable,
    severity_lookup,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"
MODES = {"tube", "elizabeth-line", "dlr", "overground", "tram"}


def load(name: str) -> Any:
    document = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert document["http_status"] == 200
    return document["payload"]


def is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


# ---- Fixtures themselves --------------------------------------------------------


def test_every_fixture_records_a_keyless_request_path() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 12
    for path in files:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
        assert document["request"]["path"].startswith("/")
        # Anywhere in the file, including URIs inside the payload.
        assert "app_key" not in text.lower()
        assert "app_id" not in text.lower()
        assert path.stat().st_size < 1024 * 1024


# ---- Line status ----------------------------------------------------------------


def test_line_statuses_parse() -> None:
    lines = parse_line_statuses(load("line_status"))
    assert len(lines) >= 15
    assert {line.mode_name for line in lines} == MODES
    assert "victoria" in {line.id for line in lines}
    for line in lines:
        assert line.line_statuses, f"{line.id} has no status"
        for status in line.line_statuses:
            assert isinstance(status.status_severity, int)
            assert status.status_severity_description
            for period in status.validity_periods:
                assert is_utc(period.from_date)
                assert is_utc(period.to_date)
        assert line.modified is None or is_utc(line.modified)


def test_a_line_can_carry_several_statuses() -> None:
    lines = parse_line_statuses(load("line_status"))
    multi = [line for line in lines if len(line.line_statuses) > 1]
    assert multi, "fixture should hold at least one line with more than one status"
    severities = [s.status_severity for s in multi[0].line_statuses]
    assert len(set(severities)) > 1


def test_line_status_disruption_and_reason_parse() -> None:
    lines = parse_line_statuses(load("line_status"))
    statuses = [s for line in lines for s in line.line_statuses]
    with_disruption = [s for s in statuses if s.disruption is not None]
    assert with_disruption
    assert all(s.disruption is not None and s.disruption.category for s in with_disruption)
    assert any(s.reason for s in statuses)


def test_empty_validity_periods_and_missing_optionals_tolerated() -> None:
    payload = copy.deepcopy(load("line_status"))
    entry = payload[0]["lineStatuses"][0]
    entry["validityPeriods"] = []
    for key in ("reason", "disruption", "created"):
        entry.pop(key, None)
    line = parse_line_statuses(payload)[0]
    assert line.line_statuses[0].validity_periods == []
    assert line.line_statuses[0].reason is None
    assert line.line_statuses[0].disruption is None


def test_unknown_fields_tolerated() -> None:
    payload = copy.deepcopy(load("line_status"))
    payload[0]["brandNewField"] = {"nested": [1, 2, 3]}
    payload[0]["lineStatuses"][0]["anotherNewField"] = "x"
    assert parse_line_statuses(payload)[0].id == payload[0]["id"]


def test_net_minimum_date_reads_as_none() -> None:
    # TfL sends 0001-01-01T00:00:00 for unset dates, without a zone.
    payload = copy.deepcopy(load("line_status"))
    payload[0]["created"] = "0001-01-01T00:00:00"
    assert parse_line_statuses(payload)[0].created is None


def test_missing_severity_is_an_error_not_good_service() -> None:
    payload = copy.deepcopy(load("line_status"))
    del payload[0]["lineStatuses"][0]["statusSeverity"]
    with pytest.raises(ValidationError):
        parse_line_statuses(payload)


def test_good_service_and_delay_with_several_validity_periods() -> None:
    # The recording was made after close of service, so build daytime shapes
    # from it: one line on Good Service, one with Minor Delays in two windows.
    payload = copy.deepcopy(load("line_status"))
    good, delayed = payload[0], payload[1]
    good_entry = good["lineStatuses"][0]
    good["lineStatuses"] = [good_entry]
    good_entry.update(statusSeverity=10, statusSeverityDescription="Good Service")
    for key in ("reason", "disruption"):
        good_entry.pop(key, None)
    good_entry["validityPeriods"] = []
    delayed_entry = delayed["lineStatuses"][0]
    delayed["lineStatuses"] = [delayed_entry]
    delayed_entry.update(statusSeverity=9, statusSeverityDescription="Minor Delays")
    delayed_entry["validityPeriods"] = [
        {"fromDate": "2026-11-03T08:00:00Z", "toDate": "2026-11-03T08:30:00Z", "isNow": True},
        {"fromDate": "2026-11-03T17:00:00Z", "toDate": "2026-11-03T18:00:00Z", "isNow": False},
    ]
    lines = parse_line_statuses([good, delayed])
    assert lines[0].line_statuses[0].status_severity == 10
    assert lines[0].line_statuses[0].validity_periods == []
    periods = lines[1].line_statuses[0].validity_periods
    assert [p.is_now for p in periods] == [True, False]
    assert periods[1].from_date == datetime(2026, 11, 3, 17, 0, tzinfo=UTC)


def test_line_with_no_statuses_parses_as_empty_not_good_service() -> None:
    # Pinned so downstream code has to treat an empty list as missing data.
    payload = copy.deepcopy(load("line_status"))
    payload[0]["lineStatuses"] = []
    line = parse_line_statuses(payload)[0]
    assert line.line_statuses == []


def test_populate_by_snake_case_name() -> None:
    line = LineStatus(id="victoria", mode_name="tube", line_statuses=[])
    assert line.model_dump(by_alias=True)["modeName"] == "tube"


# ---- Disruptions ----------------------------------------------------------------


def test_line_disruptions_parse() -> None:
    disruptions = parse_line_disruptions(load("line_disruption"))
    assert disruptions
    for disruption in disruptions:
        assert disruption.category
        assert disruption.description
        assert isinstance(disruption.affected_routes, list)


def test_stop_disruptions_parse() -> None:
    disruptions = parse_stop_disruptions(load("stop_disruption"))
    assert len(disruptions) > 10
    for disruption in disruptions:
        assert disruption.atco_code
        assert is_utc(disruption.from_date)
        assert is_utc(disruption.to_date)
        assert disruption.from_date <= disruption.to_date
    assert any(d.concerned_lines for d in disruptions)


def test_stop_disruption_naive_date_rejected() -> None:
    payload = copy.deepcopy(load("stop_disruption")[0])
    payload["fromDate"] = "2026-11-03T08:00:00"
    with pytest.raises(ValidationError):
        StopDisruption.model_validate(payload)


# ---- Arrivals -------------------------------------------------------------------


def test_arrivals_parse_with_aware_utc_datetimes() -> None:
    arrivals = parse_arrivals(load("arrivals_940GZZLUOXC"))
    assert arrivals
    for arrival in arrivals:
        assert arrival.naptan_id == "940GZZLUOXC"
        assert arrival.vehicle_id
        assert arrival.line_id
        assert isinstance(arrival.time_to_station, int)
        assert arrival.expected_arrival.tzinfo is UTC
        assert arrival.timestamp.tzinfo is UTC
        assert arrival.expected_arrival >= arrival.timestamp


def test_arrival_seven_digit_fraction_parses() -> None:
    # TfL sends .NET ticks precision (seven fractional digits).
    raw = copy.deepcopy(load("arrivals_940GZZLUOXC")[0])
    raw["timestamp"] = "2026-11-03T01:33:50.8808374Z"
    arrival = Arrival.model_validate(raw)
    assert arrival.timestamp == datetime(2026, 11, 3, 1, 33, 50, 880837, tzinfo=UTC)


def test_arrival_offset_is_normalised_to_utc() -> None:
    raw = copy.deepcopy(load("arrivals_940GZZLUOXC")[0])
    raw["expectedArrival"] = "2026-07-01T09:00:00+01:00"
    arrival = Arrival.model_validate(raw)
    assert arrival.expected_arrival == datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    assert arrival.expected_arrival.tzinfo is UTC


def test_arrival_naive_timestamp_rejected() -> None:
    raw = copy.deepcopy(load("arrivals_940GZZLUOXC")[0])
    raw["expectedArrival"] = "2026-11-03T01:53:17"
    with pytest.raises(ValidationError):
        Arrival.model_validate(raw)


@pytest.mark.parametrize("stop", ["940GZZDLCAN", "910GHGHI"])
def test_empty_arrival_board_parses(stop: str) -> None:
    # Recorded overnight: an empty list is a valid answer, not an error.
    assert parse_arrivals(load(f"arrivals_{stop}")) == []


@pytest.mark.parametrize(("line", "prefix"), [("bakerloo", "940GZZ"), ("elizabeth", "910G")])
def test_line_wide_board_parses_as_predictions_for_many_stations(line: str, prefix: str) -> None:
    # Line/{id}/Arrivals (ADR-016) uses the same Prediction shape as a station board.
    raw = load(f"line_arrivals_{line}")
    arrivals = parse_arrivals(raw)
    assert len(arrivals) == len(raw) > 0
    assert {a.line_id for a in arrivals} == {line}
    assert len({a.naptan_id for a in arrivals}) > 1
    assert all(a.naptan_id.startswith(prefix) for a in arrivals)
    assert all(is_utc(a.expected_arrival) and is_utc(a.timestamp) for a in arrivals)


def test_empty_line_wide_board_parses() -> None:
    # Liberty recorded overnight: no trains is an empty list, not an error.
    assert parse_arrivals(load("line_arrivals_liberty")) == []


# ---- Route sequence -------------------------------------------------------------


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_route_sequence_parses(direction: str) -> None:
    sequence = parse_route_sequence(load(f"route_sequence_victoria_{direction}"))
    assert isinstance(sequence, RouteSequence)
    assert sequence.line_id == "victoria"
    assert sequence.direction == direction
    route = sequence.ordered_line_routes[0]
    assert "940GZZLUOXC" in route.naptan_ids
    assert {"940GZZLUWWL", "940GZZLUBXN"} <= set(route.naptan_ids)
    assert sequence.stop_point_sequences
    stops = sequence.stop_point_sequences[0].stop_point
    assert [s.id for s in stops] == route.naptan_ids
    assert all(s.name for s in sequence.stations)


# ---- Timetable ------------------------------------------------------------------


def test_timetable_without_direction_is_a_disambiguation() -> None:
    timetable = parse_timetable(load("timetable_victoria_940GZZLUOXC"))
    assert timetable.timetable is None
    assert timetable.disambiguation is not None
    uris = {o.uri for o in timetable.disambiguation.disambiguation_options}
    assert any(uri.endswith("direction=inbound") for uri in uris)
    assert any(uri.endswith("direction=outbound") for uri in uris)


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_timetable_parses(direction: str) -> None:
    timetable = parse_timetable(load(f"timetable_victoria_940GZZLUOXC_{direction}"))
    assert timetable.line_id == "victoria"
    assert timetable.direction == direction
    assert timetable.timetable is not None
    assert timetable.timetable.departure_stop_id == "940GZZLUOXC"
    route = timetable.timetable.routes[0]
    assert route.station_intervals
    names = {s.name for s in route.schedules}
    assert any("Monday" in name for name in names)
    journeys = [j for s in route.schedules for j in s.known_journeys]
    assert journeys
    assert all(isinstance(j.hour, int) and 0 <= j.minute <= 59 for j in journeys)
    interval_ids = {int(i.id) for i in route.station_intervals}
    assert {j.interval_id for j in journeys} <= interval_ids


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "$type": "Tfl.Api.Presentation.Entities.ApiError, Tfl.Api.Presentation.Entities",
            "httpStatusCode": 404,
            "message": "not found",
        },
    ],
)
def test_timetable_error_body_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse_timetable(payload)


def test_timetable_hours_run_past_midnight() -> None:
    timetable = parse_timetable(load("timetable_victoria_940GZZLUOXC_inbound"))
    assert timetable.timetable is not None
    hours = [j.hour for s in timetable.timetable.routes[0].schedules for j in s.known_journeys]
    assert max(hours) >= 24


# ---- Severity -------------------------------------------------------------------


def test_severities_parse_and_include_good_service() -> None:
    severities = parse_severities(load("severity"))
    lookup = severity_lookup(severities)
    for mode in MODES:
        assert lookup[(mode, 10)] == "Good Service"
    assert {s.mode_name for s in severities} >= MODES


def test_severity_codes_are_per_mode() -> None:
    lookup = severity_lookup(parse_severities(load("severity")))
    assert lookup[("tube", 20)] == "Service Closed"
    assert lookup[("national-rail", 20)] == "No Service"
    assert lookup[("road", 10)] != "Good Service"


def test_severity_lookup_rejects_conflicting_descriptions() -> None:
    same = [
        Severity(mode_name="tube", severity_level=10, description="Good Service"),
        Severity(mode_name="tube", severity_level=10, description="Good Service"),
    ]
    assert severity_lookup(same) == {("tube", 10): "Good Service"}
    conflicting = [*same, Severity(mode_name="tube", severity_level=10, description="Other")]
    with pytest.raises(ValueError, match="conflicting"):
        severity_lookup(conflicting)


def test_every_status_severity_is_in_the_severity_reference() -> None:
    lookup = severity_lookup(parse_severities(load("severity")))
    for line in parse_line_statuses(load("line_status")):
        for status in line.line_statuses:
            key = (line.mode_name, status.status_severity)
            assert lookup[key] == status.status_severity_description
