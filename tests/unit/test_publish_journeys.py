"""Should I Run files (ADR-018, D76): journeys/index.json and the origin files."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from mind_the_data.publish import export
from mind_the_data.publish.documents import Stamp
from mind_the_data.publish.journeys import (
    JourneyRows,
    JourneySlotRow,
    ReachRow,
    clock,
    journey_documents,
    load_recorded_journeys,
)
from mind_the_data.publish.marts import RecordedMarts, Recording, dump_recording
from mind_the_data.publish.schemas import validate
from mind_the_data.publish.upload import classify

ROOT = Path(__file__).resolve().parents[2]
STAMP = Stamp(generated_at=datetime(2026, 11, 20, 6, 0, tzinfo=UTC), git_sha="0123abc")
KSX, VIC, GPK, BRX = "940GZZLUKSX", "940GZZLUVIC", "940GZZLUGPK", "940GZZLUBXN"


def slot(start: int, status: str, **overrides: Any) -> JourneySlotRow:
    values: dict[str, Any] = {
        "line_id": "victoria",
        "direction": "inbound",
        "origin_naptan_id": KSX,
        "destination_naptan_id": VIC,
        "day_class": "weekday",
        "slot_start_minute": start,
        "slot_end_minute": start + 15,
        "days_counted": 20,
        "passes_gate": True,
        "runs": 160,
        "weeks_with_runs": 4,
        "run_p50_seconds": 552,
        "run_p90_seconds": 614,
        "trains_seen_per_day": 7.84,
        "scheduled_per_day": 9.0,
        "typical_wait_seconds": 60.4,
        "worst_wait_p90_seconds": 226,
        "status": status,
        "first_london_date": date(2026, 11, 4),
        "last_london_date": date(2026, 12, 1),
    }
    values.update(overrides)
    return JourneySlotRow(**values)


def reach(destination: str, name: str, train: str, train_name: str, stops: int) -> ReachRow:
    return ReachRow(
        line_id="victoria",
        direction="inbound",
        origin_naptan_id=KSX,
        origin_name="King's Cross St. Pancras",
        destination_naptan_id=destination,
        destination_name=name,
        train_destination_naptan_id=train,
        train_destination_name=train_name,
        stops_to_destination=stops,
        is_ambiguous=False,
    )


ROWS = JourneyRows(
    slots=[
        slot(480, "ok"),
        slot(495, "not_enough_data", runs=12, weeks_with_runs=2),
        slot(480, "not_enough_data", destination_naptan_id=GPK, passes_gate=False, runs=0),
    ],
    reach=[
        reach(VIC, "Victoria", VIC, "Victoria", 5),
        reach(VIC, "Victoria", BRX, "Brixton", 5),
        reach(GPK, "Green Park", BRX, "Brixton", 4),
    ],
)


def by_path() -> dict[str, dict[str, Any]]:
    return {path: doc for path, doc, _ in journey_documents(ROWS, STAMP)}


def test_every_journey_document_validates() -> None:
    documents = journey_documents(ROWS, STAMP)
    assert [path for path, _, _ in documents] == [
        "journeys/index.json",
        f"journeys/victoria/inbound/{KSX}.json",
    ]
    for _, document, schema in documents:
        validate(document, schema)


def test_figures_are_published_only_for_ok_slots() -> None:
    doc = by_path()[f"journeys/victoria/inbound/{KSX}.json"]
    destinations = {d["naptan_id"]: d for d in doc["destinations"]}
    ok, thin = destinations[VIC]["slots"]
    assert ok == {
        "day_class": "weekday",
        "start": "08:00",
        "end": "08:15",
        "status": "ok",
        "days_counted": 20,
        "passes_gate": True,
        "runs": 160,
        "weeks_with_runs": 4,
        "run_seconds": {"p50": 552, "p90": 614},
        "wait_seconds": {"typical": 60.0, "worst_p90": 226},
        "trains_seen_per_day": 7.8,
        "scheduled_per_day": 9.0,
    }
    # Not enough data is never a quiet slot: the counts stay, every figure is null.
    assert thin["status"] == "not_enough_data" and thin["runs"] == 12
    for key in ("run_seconds", "wait_seconds", "trains_seen_per_day", "scheduled_per_day"):
        assert thin[key] is None
    gate_failed = destinations[GPK]["slots"][0]
    assert gate_failed["passes_gate"] is False and gate_failed["run_seconds"] is None


def test_destinations_follow_the_route_and_list_board_destinations() -> None:
    doc = by_path()[f"journeys/victoria/inbound/{KSX}.json"]
    destinations = doc["destinations"]
    assert [d["naptan_id"] for d in destinations] == [GPK, VIC]
    assert destinations[1]["train_destinations"] == [
        {"naptan_id": BRX, "name": "Brixton", "is_ambiguous": False},
        {"naptan_id": VIC, "name": "Victoria", "is_ambiguous": False},
    ]
    assert doc["attribution"] == "Powered by TfL Open Data"
    assert doc["disclaimer"] == "Estimates, not guarantees."
    assert doc["data_window"] == {
        "start": "2026-11-04",
        "end": "2026-12-01",
        "timezone": "Europe/London",
    }


def test_index_counts_published_slots_and_names_stations() -> None:
    index = by_path()["journeys/index.json"]
    assert index["origins"] == [
        {
            "line_id": "victoria",
            "direction": "inbound",
            "origin_naptan_id": KSX,
            "path": f"journeys/victoria/inbound/{KSX}.json",
            "destination_naptan_ids": [GPK, VIC],
            "slots_published": 1,
        }
    ]
    assert index["stations"] == {
        BRX: "Brixton",
        GPK: "Green Park",
        KSX: "King's Cross St. Pancras",
        VIC: "Victoria",
    }


def test_an_origin_with_reach_but_no_slots_still_gets_a_file() -> None:
    rows = JourneyRows(slots=[], reach=ROWS.reach)
    documents = journey_documents(rows, STAMP)
    for _, document, schema in documents:
        validate(document, schema)
    origin = {p: d for p, d, _ in documents}[f"journeys/victoria/inbound/{KSX}.json"]
    assert all(d["slots"] == [] for d in origin["destinations"])
    assert origin["data_window"]["start"] is None


@pytest.mark.parametrize(("minute", "text"), [(0, "00:00"), (360, "06:00"), (1125, "18:45")])
def test_clock(minute: int, text: str) -> None:
    assert clock(minute) == text


def test_upload_accepts_journey_paths() -> None:
    assert classify("journeys/index.json")[0].startswith("application/json")
    assert classify(f"journeys/hammersmith-city/outbound/{KSX}.json")[0].startswith(
        "application/json"
    )
    with pytest.raises(Exception, match="journeys"):
        classify("journeys/victoria/sideways/x.json")


def test_export_writes_journeys_only_when_given_them() -> None:
    marts = RecordedMarts(ROOT / "tests" / "fixtures" / "marts")
    week = date(2026, 11, 2)
    _, without = export.build_outputs(marts, week, STAMP)
    assert not any(o.path.startswith("journeys/") for o in without)
    _, with_journeys = export.build_outputs(marts, week, STAMP, journeys=ROWS)
    paths = {o.path for o in with_journeys}
    assert {"journeys/index.json", f"journeys/victoria/inbound/{KSX}.json"} <= paths
    # The flag adds files and changes nothing else.
    assert {o.path for o in without} == paths - {p for p in paths if p.startswith("journeys/")}


def test_recorded_journeys_round_trip(tmp_path: Path) -> None:
    assert load_recorded_journeys(tmp_path) is None
    dump_recording(
        tmp_path / "journey_slots.json",
        Recording(
            columns=[
                "line_id", "direction", "origin_naptan_id", "destination_naptan_id",
                "day_class", "slot_start_minute", "slot_end_minute", "days_counted",
                "passes_gate", "runs", "weeks_with_runs", "run_p50_seconds",
                "run_p90_seconds", "trains_seen_per_day", "scheduled_per_day",
                "typical_wait_seconds", "worst_wait_p90_seconds", "status",
                "first_london_date", "last_london_date",
            ],
            rows=[[
                "victoria", "inbound", KSX, VIC, "weekday", "480", "495", "20", "true",
                "160", "4", "552", "614", "7.84", None, "60.4", "226", "ok",
                "2026-11-04", "2026-12-01",
            ]],
        ),
    )  # fmt: skip
    dump_recording(
        tmp_path / "station_reach.json",
        Recording(
            columns=[
                "line_id", "direction", "origin_naptan_id", "origin_name",
                "destination_naptan_id", "destination_name", "train_destination_naptan_id",
                "train_destination_name", "stops_to_destination", "is_ambiguous",
            ],
            rows=[[
                "victoria", "inbound", KSX, "King's Cross St. Pancras", VIC, "Victoria",
                BRX, "Brixton", "5", "false",
            ]],
        ),
    )  # fmt: skip
    rows = load_recorded_journeys(tmp_path)
    assert rows is not None
    assert rows.slots[0].scheduled_per_day is None and rows.slots[0].passes_gate is True
    assert rows.reach[0].is_ambiguous is False
    doc = {p: d for p, d, _ in journey_documents(rows, STAMP)}[
        f"journeys/victoria/inbound/{KSX}.json"
    ]
    assert json.loads(json.dumps(doc))["destinations"][0]["slots"][0]["scheduled_per_day"] is None
