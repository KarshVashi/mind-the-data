"""Station context feeds (ADR-019): plans, parsers and the station list.

Pure: no network, no AWS. Payloads come from recorded fixtures.
"""

import copy
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mind_the_data.common.config import (
    MAX_CROWDING_STATIONS,
    ConfigError,
    CrowdingStation,
    Line,
    load_config,
    load_crowding_stations,
    max_calls_per_poll,
)
from mind_the_data.common.raw import Feed
from mind_the_data.ingest import context, models
from mind_the_data.ingest.collect import count_records

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tfl"
# The key limit (PLAN 5.3) and the reference job's peak rate: 197 calls on a
# Monday at concurrency 4 over about 4 minutes (ADR-007, ADR-016).
KEY_LIMIT_PER_MINUTE = 500
REFERENCE_PEAK_PER_MINUTE = 50


def _payload(name: str) -> Any:
    document = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert document["http_status"] == 200
    return document["payload"]


@pytest.fixture(scope="module")
def lines() -> list[Line]:
    return load_config()[0]


@pytest.fixture(scope="module")
def stations() -> list[CrowdingStation]:
    return load_crowding_stations()


def _slot(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 20, hour, minute, tzinfo=UTC)


def _feeds(planned: list[Any]) -> Counter[Feed]:
    return Counter(p.feed for p in planned)


# ---- Plans ------------------------------------------------------------------------


def test_every_run_reads_the_lifts_once(lines: list[Line], stations: list[CrowdingStation]) -> None:
    for minute in range(60):
        planned = context.plan_context(lines, stations, _slot(9, minute))
        assert _feeds(planned)[Feed.LIFT_DISRUPTION] == 1
        assert planned[0].path == "/Disruptions/Lifts/v2"


def test_each_station_is_read_once_every_five_minutes(
    lines: list[Line], stations: list[CrowdingStation]
) -> None:
    seen: Counter[str] = Counter()
    for minute in range(10, 15):
        for p in context.plan_context(lines, stations, _slot(9, minute)):
            if p.feed is Feed.CROWDING:
                seen[p.path] += 1
    assert set(seen.values()) == {1}
    assert set(seen) == {f"/crowding/{s.naptan_id}/Live" for s in stations}


def test_crowding_slices_are_pinned() -> None:
    # A station keeps its minute across releases: CRC-32, not the salted hash().
    ids = ("940GZZLUKSX", "940GZZLUOXC", "940GZZLUWLO")
    assert [context.crowding_slice(n) for n in ids] == [1, 3, 1]


def test_bike_points_every_fifth_minute_away_from_stop_disruption(
    lines: list[Line], stations: list[CrowdingStation]
) -> None:
    minutes = [
        m
        for m in range(60)
        if _feeds(context.plan_context(lines, stations, _slot(9, m)))[Feed.BIKE_POINT]
    ]
    assert minutes == list(range(2, 60, 5))
    # The poller reads stop disruption on minute % 5 == 0.
    assert all(m % 5 != 0 for m in minutes)


def test_status_ahead_four_times_a_day(lines: list[Line], stations: list[CrowdingStation]) -> None:
    runs = [
        (h, m)
        for h in range(24)
        for m in range(60)
        if _feeds(context.plan_context(lines, stations, _slot(h, m)))[Feed.LINE_STATUS_AHEAD]
    ]
    assert runs == [(0, 7), (6, 7), (12, 7), (18, 7)]


def test_status_ahead_names_every_line_and_fourteen_days(lines: list[Line]) -> None:
    path = context.ahead_path(lines, _slot(18, 7))
    ids = ",".join(line.id for line in lines)
    assert path == f"/Line/{ids}/Status/2026-11-20/to/2026-12-04"
    # The Mode form answers 404 (probed 2026-11-20).
    assert "/Mode/" not in path


def test_largest_run_and_combined_rate_stay_well_inside_the_key_limit(
    lines: list[Line], stations: list[CrowdingStation]
) -> None:
    per_run = [
        len(context.plan_context(lines, stations, _slot(h, m)))
        for h in range(24)
        for m in range(60)
    ]
    largest = max(per_run)
    # 63 stations in the largest slice, lifts, BikePoint or status ahead.
    assert largest <= 70
    # Averaged over a day: about 54 busyness calls, 1 lift call, 0.2 BikePoint.
    assert sum(per_run) / len(per_run) == pytest.approx(55.2, abs=0.5)
    _, measurement = load_config()
    poller_peak = max_calls_per_poll(lines, measurement)
    peak_minute = poller_peak + largest + REFERENCE_PEAK_PER_MINUTE
    assert peak_minute <= KEY_LIMIT_PER_MINUTE * 0.4


# ---- Station list -------------------------------------------------------------------


def test_station_list_is_every_tube_station_within_budget(
    stations: list[CrowdingStation],
) -> None:
    assert 260 <= len(stations) <= MAX_CROWDING_STATIONS
    ids = {s.naptan_id for s in stations}
    assert {"940GZZLUKSX", "940GZZLUOXC", "940GZZLUBST"} <= ids
    assert all(i.startswith("940GZZLU") for i in ids)


def _write(tmp_path: Path, stations: list[dict[str, str]]) -> Path:
    import yaml

    path = tmp_path / "crowding_stations.yaml"
    path.write_text(yaml.safe_dump({"stations": stations}))
    return path


@pytest.mark.parametrize(
    "entries",
    [
        [{"naptan_id": "940GZZLUKSX", "name": "A"}, {"naptan_id": "940GZZLUKSX", "name": "B"}],
        [{"naptan_id": "HUBKGX", "name": "Hub ids group several stations"}],
        [{"naptan_id": "910GPADTLL", "name": "Elizabeth line is out of scope"}],
        [],
    ],
)
def test_bad_station_lists_are_rejected(tmp_path: Path, entries: list[dict[str, str]]) -> None:
    with pytest.raises(ConfigError):
        load_crowding_stations(_write(tmp_path, entries))


def test_too_many_stations_is_rejected(tmp_path: Path) -> None:
    entries = [
        {"naptan_id": f"940GZZLU{i:04d}", "name": "x"} for i in range(MAX_CROWDING_STATIONS + 1)
    ]
    with pytest.raises(ConfigError, match="budget"):
        load_crowding_stations(_write(tmp_path, entries))


# ---- Parsers -------------------------------------------------------------------------


def test_lift_disruptions_parse() -> None:
    lifts = models.parse_lift_disruptions(_payload("lift_disruption"))
    assert lifts
    for lift in lifts:
        assert lift.station_unique_id
        assert lift.disrupted_lift_unique_ids
        assert all(i.startswith(lift.station_unique_id) for i in lift.disrupted_lift_unique_ids)
    assert count_records(Feed.LIFT_DISRUPTION, _payload("lift_disruption")) == (len(lifts), 0)


def test_an_empty_lift_list_is_a_valid_response() -> None:
    # No lifts out of service is a real answer, unlike a failed request.
    assert count_records(Feed.LIFT_DISRUPTION, []) == (0, 0)


def test_live_busyness_parses_with_a_utc_time() -> None:
    live = models.parse_crowding_live(_payload("crowding_live_940GZZLUKSX"))
    assert live.data_available
    assert live.time_utc is not None
    assert live.time_utc.utcoffset() == timedelta(0)
    assert live.share_of_baseline == live.percentage_of_baseline
    assert count_records(Feed.CROWDING, _payload("crowding_live_940GZZLUKSX")) == (1, 0)


def test_live_busyness_without_data_is_missing_not_empty() -> None:
    # Recorded for 940GZZLUOXC at 03:26 UTC on 2026-11-20.
    unavailable = {
        "dataAvailable": False,
        "percentageOfBaseline": 0,
        "timeUtc": None,
        "timeLocal": None,
    }
    live = models.parse_crowding_live(unavailable)
    assert live.share_of_baseline is None
    assert live.time_utc is None
    assert count_records(Feed.CROWDING, unavailable) == (1, 0)


@pytest.mark.parametrize("payload", [[], "not json", {"percentageOfBaseline": 0.5}])
def test_unreadable_busyness_is_invalid(payload: Any) -> None:
    assert count_records(Feed.CROWDING, payload) == (0, 1)


def test_bike_points_parse_with_counts() -> None:
    docks = models.parse_bike_points(_payload("bike_point"))
    assert docks
    for dock in docks:
        assert dock.id.startswith("BikePoints_")
        bikes = dock.count("NbBikes")
        empty = dock.count("NbEmptyDocks")
        total = dock.count("NbDocks")
        assert bikes is not None and empty is not None and total is not None
        # Broken docks are why bikes + empty can fall short of the total.
        assert bikes + empty <= total
        assert dock.count("NbStandardBikes") is not None
        assert dock.count("NbEBikes") is not None
    assert count_records(Feed.BIKE_POINT, _payload("bike_point")) == (len(docks), 0)


def test_a_missing_or_garbled_count_is_none() -> None:
    dock = copy.deepcopy(_payload("bike_point")[0])
    for prop in dock["additionalProperties"]:
        if prop["key"] == "NbBikes":
            prop["value"] = ""
    parsed = models.BikePoint.model_validate(dock)
    assert parsed.count("NbBikes") is None
    assert parsed.count("NoSuchKey") is None


def test_status_ahead_parses_as_line_statuses_with_future_periods() -> None:
    payload = _payload("line_status_ahead")
    lines = models.parse_line_statuses(payload)
    periods = [p for line in lines for s in line.line_statuses for p in s.validity_periods]
    assert any(not p.is_now for p in periods)
    assert any(
        s.disruption is not None and s.disruption.category == "PlannedWork"
        for line in lines
        for s in line.line_statuses
    )
    assert count_records(Feed.LINE_STATUS_AHEAD, payload) == (len(lines), 0)
