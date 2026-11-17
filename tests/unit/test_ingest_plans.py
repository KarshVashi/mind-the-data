"""Request plans for the poller and the reference fetch (M8, ADR-016).

Pure planning only: no network, no AWS. Route sequences come from the
recorded Victoria fixtures.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mind_the_data.common.config import (
    Line,
    Mode,
    Station,
    StationRole,
    distinct_station_ids,
    load_config,
    max_calls_per_poll,
)
from mind_the_data.common.raw import Feed
from mind_the_data.ingest import poller, reference
from mind_the_data.ingest.client import RequestResult
from mind_the_data.ingest.collect import PlannedRequest, count_records

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tfl"
MONDAY = date(2026, 11, 16)
VICTORIA_MEASURED = {"940GZZLUVIC", "940GZZLUGPK", "940GZZLUOXC", "940GZZLUKSX"}


def _payload(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())["payload"]


@pytest.fixture(scope="module")
def config() -> tuple[list[Line], list[Station]]:
    return load_config()


def _line(line_id: str, mode: Mode) -> Line:
    return Line(id=line_id, name=line_id, mode=mode, colour="#000000", low_volume=False)


def _station(line_id: str, naptan_id: str) -> Station:
    return Station(
        line_id=line_id, naptan_id=naptan_id, name=naptan_id, role=StationRole.CORE, reason="t"
    )


def _route_response(
    line_id: str, direction: str, payload: Any, status: int = 200
) -> tuple[PlannedRequest, RequestResult]:
    path = f"/Line/{line_id}/Route/Sequence/{direction}"
    error = None if status == 200 else f"http_{status}"
    return (
        PlannedRequest(Feed.REF_ROUTE_SEQUENCE, path),
        RequestResult(path, status, 10, error, payload if status == 200 else None, 1),
    )


def _victoria_responses() -> list[tuple[PlannedRequest, RequestResult]]:
    return [
        _route_response("victoria", d, _payload(f"route_sequence_victoria_{d}"))
        for d in reference.DIRECTIONS
    ]


# ---- Poller --------------------------------------------------------------------


def test_poll_plans_one_line_wide_board_per_line(config: tuple[list[Line], list[Station]]) -> None:
    lines, stations = config
    planned = poller.plan_poll(lines, stations, datetime(2026, 11, 17, 8, 16, tzinfo=UTC))

    line_boards = [p for p in planned if p.feed is Feed.LINE_ARRIVALS]
    assert [p.path for p in line_boards] == [f"/Line/{line.id}/Arrivals" for line in lines]
    assert all(p.params is None for p in line_boards)
    station_boards = [p for p in planned if p.feed is Feed.ARRIVALS]
    assert len(station_boards) == len(distinct_station_ids(stations))
    # 2 line-level feeds + 20 line boards + 50 station boards.
    assert len(planned) == 72


@pytest.mark.parametrize("minute", [16, 20])
def test_line_boards_are_queued_after_every_other_request(
    config: tuple[list[Line], list[Station]], minute: int
) -> None:
    # Slots are taken in plan order, so the established feeds start first.
    lines, stations = config
    slot = datetime(2026, 11, 17, 8, minute, tzinfo=UTC)
    feeds = [p.feed for p in poller.plan_poll(lines, stations, slot)]
    first_line_board = feeds.index(Feed.LINE_ARRIVALS)
    assert set(feeds[first_line_board:]) == {Feed.LINE_ARRIVALS}
    assert len(feeds[first_line_board:]) == len(lines)


def test_fifth_minute_poll_is_the_largest_and_within_budget(
    config: tuple[list[Line], list[Station]],
) -> None:
    lines, stations = config
    planned = poller.plan_poll(lines, stations, datetime(2026, 11, 17, 8, 20, tzinfo=UTC))
    assert len(planned) == max_calls_per_poll(lines, stations) == 73
    assert sum(p.feed is Feed.STOP_DISRUPTION for p in planned) == 1


def test_line_wide_records_are_counted_with_the_arrival_model() -> None:
    board = _payload("line_arrivals_bakerloo")
    assert count_records(Feed.LINE_ARRIVALS, board) == (len(board), 0)
    assert count_records(Feed.LINE_ARRIVALS, [*board, {"id": "broken"}]) == (len(board), 1)
    assert count_records(Feed.LINE_ARRIVALS, []) == (0, 0)
    assert count_records(Feed.LINE_ARRIVALS, {"message": "not a list"}) == (0, 1)


# ---- Reference: first round ------------------------------------------------------


def test_monday_fetches_measurement_timetables_as_before(
    config: tuple[list[Line], list[Station]],
) -> None:
    lines, stations = config
    planned = reference.plan_reference(lines, stations, MONDAY)
    timetables = [p for p in planned if p.feed is Feed.REF_TIMETABLE]
    assert len(timetables) == 2 * len(stations)
    assert sum(p.feed is Feed.REF_ROUTE_SEQUENCE for p in planned) == 2 * len(lines)
    assert sum(p.feed is Feed.REF_SEVERITY for p in planned) == 1


@pytest.mark.parametrize("offset", range(1, 7))
def test_other_days_fetch_no_measurement_timetables(
    config: tuple[list[Line], list[Station]], offset: int
) -> None:
    lines, stations = config
    planned = reference.plan_reference(lines, stations, MONDAY + timedelta(days=offset))
    assert not [p for p in planned if p.feed is Feed.REF_TIMETABLE]
    assert len(planned) == 1 + 2 * len(lines)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"scheduled_time": "2026-11-17T03:00:00Z"}, date(2026, 11, 17)),
        # 03:00 London in summer is 02:00 UTC, the same date.
        ({"scheduled_time": "2026-06-02T02:00:00+00:00"}, date(2026, 6, 2)),
        ({"scheduled_time": "garbage"}, date(2026, 11, 20)),
        ({}, date(2026, 11, 20)),
    ],
)
def test_run_date_follows_the_schedule(event: dict[str, Any], expected: date) -> None:
    assert reference.run_date(event, datetime(2026, 11, 20, 9, tzinfo=UTC)) == expected


# ---- Reference: station list and slices ------------------------------------------


def test_network_weekday_is_pinned() -> None:
    # Changing the hash reshuffles every station's day: a station could then
    # go up to two weeks without a fetch. These values must not move.
    assert reference.network_weekday("victoria", "940GZZLUWWL") == 1
    assert reference.network_weekday("victoria", "940GZZLUBXN") == 5
    assert reference.network_weekday("dlr", "940GZZDLCAN") == 3
    assert reference.network_weekday("northern", "940GZZLUMDN") == 4


def test_network_days_never_include_monday() -> None:
    days = {reference.network_weekday("tram", f"940GZZCR{n:03d}") for n in range(500)}
    assert days == set(reference.NETWORK_WEEKDAYS) == {1, 2, 3, 4, 5, 6}


def test_line_stations_come_from_both_directions_route_sequences() -> None:
    victoria = _line("victoria", Mode.TUBE)
    found = reference.line_stations([victoria], _victoria_responses())
    assert set(found) == {"victoria"}
    assert len(found["victoria"]) == 16
    assert VICTORIA_MEASURED <= found["victoria"]


def test_line_stations_skip_failed_unparsed_and_unplanned_responses() -> None:
    lines = [_line("victoria", Mode.TUBE), _line("central", Mode.TUBE)]
    responses = [
        _route_response("victoria", "inbound", {"not": "a route sequence"}),
        _route_response("victoria", "outbound", None, status=500),
        # A line not in config is never planned, even if a response turns up.
        _route_response("jubilee", "inbound", _payload("route_sequence_victoria_inbound")),
        _route_response("central", "inbound", _payload("route_sequence_victoria_inbound")),
    ]
    found = reference.line_stations(lines, responses)
    # The line comes from the request, not from the payload's own lineId.
    assert set(found) == {"central"}
    assert len(found["central"]) == 16


def test_slices_cover_each_non_measurement_station_once_a_week() -> None:
    victoria = _line("victoria", Mode.TUBE)
    measured = [_station("victoria", n) for n in sorted(VICTORIA_MEASURED)]
    by_line = reference.line_stations([victoria], _victoria_responses())

    per_day = {
        offset: reference.plan_network_timetables(
            [victoria], measured, MONDAY + timedelta(days=offset), by_line
        )
        for offset in range(7)
    }
    assert per_day[0] == []  # Monday belongs to the measurement stations.

    fetched: list[tuple[str, str]] = []
    for offset, planned in per_day.items():
        for p in planned:
            assert p.feed is Feed.REF_TIMETABLE
            naptan = p.path.rsplit("/", 1)[1]
            assert reference.network_weekday("victoria", naptan) == offset
            assert p.params is not None
            fetched.append((naptan, p.params["direction"]))
    # 16 stations less 4 measurement stations, both directions, exactly once.
    assert len(fetched) == len(set(fetched)) == 24
    assert {n for n, _ in fetched} == by_line["victoria"] - VICTORIA_MEASURED


def test_slices_are_the_same_every_week() -> None:
    victoria = _line("victoria", Mode.TUBE)
    by_line = reference.line_stations([victoria], _victoria_responses())
    for offset in range(7):
        day = MONDAY + timedelta(days=offset)
        assert reference.plan_network_timetables(
            [victoria], [], day, by_line
        ) == reference.plan_network_timetables([victoria], [], day + timedelta(weeks=5), by_line)


def test_modes_without_timetables_are_left_out_of_the_network_fetch() -> None:
    stations = {"some-line": {f"910GSTN{n:03d}" for n in range(60)}}
    for mode in (Mode.OVERGROUND, Mode.ELIZABETH_LINE):
        line = _line("some-line", mode)
        for offset in range(7):
            day = MONDAY + timedelta(days=offset)
            assert reference.plan_network_timetables([line], [], day, stations) == []


def test_follow_up_plans_from_the_first_round(config: tuple[list[Line], list[Station]]) -> None:
    lines, stations = config
    tuesday = MONDAY + timedelta(days=1)
    plan = reference.network_follow_up(lines, stations, tuesday)
    planned = plan(_victoria_responses())
    naptans = {p.path.rsplit("/", 1)[1] for p in planned}
    assert all(p.path.startswith("/Line/victoria/Timetable/") for p in planned)
    assert naptans == {
        n
        for n in reference.line_stations(lines, _victoria_responses())["victoria"]
        if n not in VICTORIA_MEASURED and reference.network_weekday("victoria", n) == 1
    }
