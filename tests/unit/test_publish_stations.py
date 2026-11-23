"""stations/index.json, stations/{naptan_id}.json and the stations block in meta/index.json."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import ValidationError

from mind_the_data.publish import export, upload
from mind_the_data.publish.documents import Stamp
from mind_the_data.publish.marts import (
    AthenaMarts,
    RecordedMarts,
    StationDisruptionRow,
    StationInfo,
    StationMeasuredRow,
    parse_station,
)
from mind_the_data.publish.schemas import SCHEMA_VERSION, validate

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "marts"
STAMP = Stamp(generated_at=datetime(2026, 11, 16, 6, 30, tzinfo=UTC), git_sha="0123abc")
W46 = date(2026, 11, 9)
W45 = date(2026, 11, 2)


def docs(marts: RecordedMarts, week: date = W46, *, enabled: bool = True) -> dict[str, dict]:  # type: ignore[type-arg]
    _, outputs = export.build_outputs(
        marts, week, STAMP, allow_incomplete=True, stations_enabled=enabled
    )
    return {o.path: o.document for o in outputs}


def test_schema_version_is_0_9_0() -> None:
    assert SCHEMA_VERSION == "0.9.0"


def test_array_cells_parse() -> None:
    station = parse_station(
        {
            "naptan_id": "x1",
            "station_name": "X",
            "lat": "51.5",
            "lon": "-0.1",
            "line_ids": "[a, b]",
            "modes": "[]",
            "is_measurement_station": "false",
        }
    )
    assert station.line_ids == ("a", "b") and station.modes == ()


def test_every_station_has_a_file_and_the_index_lists_them() -> None:
    d = docs(RecordedMarts(FIXTURES))
    ids = sorted(p.split("/")[1][:-5] for p in d if p.startswith("stations/") and "index" not in p)
    assert ids == [
        "910GKNGX",
        "940GZZDLBNK",
        "940GZZLUBNK",
        "940GZZLUOXC",
        "940GZZLUUPK",
    ]
    block = d["meta/index.json"]["stations"]
    assert block["naptan_ids"] == ids and block["count"] == 5
    assert block["index_path"] == "stations/index.json"
    assert [s["naptan_id"] for s in d["stations/index.json"]["stations"]] == ids
    assert all(
        s["path"] == f"stations/{s['naptan_id']}.json" for s in d["stations/index.json"]["stations"]
    )


def test_summary_rolls_up_the_latest_week() -> None:
    index = docs(RecordedMarts(FIXTURES))["stations/index.json"]
    by_id = {s["naptan_id"]: s for s in index["stations"]}
    oxc = by_id["940GZZLUOXC"]["summary"]
    # Two measured directions and one withheld: measured, with the reason kept.
    assert oxc["measured_status"] == "measured"
    assert (oxc["directions_measured"], oxc["directions_total"]) == (2, 3)
    assert oxc["status_reasons"] == ["gate_failed"]
    assert oxc["disruption"]["step_free_hours"] == 40.0
    bnk = by_id["940GZZLUBNK"]["summary"]
    assert bnk["measured_status"] == "not_measured"
    assert bnk["status_reasons"] == ["too_few_observed_hours"]
    # No line_arrivals rows: not measured, and never an implied good service.
    upk = by_id["940GZZLUUPK"]["summary"]
    assert upk["measured_status"] == "not_measured"
    assert upk["status_reasons"] == ["no_line_arrivals_data"]
    assert upk["disruption"]["closure_hours"] == 0.0
    # No disruption row means unobserved, not zero hours.
    assert by_id["940GZZDLBNK"]["summary"]["disruption"] is None


def test_detail_history_per_line_and_direction() -> None:
    doc = docs(RecordedMarts(FIXTURES))["stations/940GZZLUOXC.json"]
    keys = [(h["line_id"], h["direction"]) for h in doc["measured_history"]]
    assert keys == [
        ("central", "eastbound"),
        ("victoria", "northbound"),
        ("victoria", "southbound"),
    ]
    central = doc["measured_history"][0]["weeks"][0]
    assert central["measured_status"] == "withheld"
    assert central["measured_withheld_reason"] == "gate_failed"
    assert central["excess_wait_minutes"] is None
    assert [d["iso_week"] for d in doc["disruption_history"]] == ["2026-W45", "2026-W46"]
    assert doc["station"]["lines"] == ["bakerloo", "central", "victoria"]


def test_export_week_hides_later_weeks() -> None:
    doc = docs(RecordedMarts(FIXTURES), W45)["stations/940GZZLUOXC.json"]
    assert doc["measured_history"] == []
    assert doc["summary"]["measured_status"] == "not_measured"
    assert [d["iso_week"] for d in doc["disruption_history"]] == ["2026-W45"]


class NoStations(RecordedMarts):
    def stations(self) -> list[StationInfo]:
        return []

    def station_disruption_weekly(self, end: date) -> list[StationDisruptionRow]:
        return []

    def station_measured_weekly(self, end: date) -> list[StationMeasuredRow]:
        return []


def test_empty_source_still_exports() -> None:
    d = docs(NoStations(FIXTURES))
    assert d["stations/index.json"]["stations"] == []
    assert d["meta/index.json"]["stations"]["count"] == 0


class NoArrivals(RecordedMarts):
    def station_measured_weekly(self, end: date) -> list[StationMeasuredRow]:
        return []

    def station_disruption_weekly(self, end: date) -> list[StationDisruptionRow]:
        return []


def test_stations_without_arrivals_say_not_measured() -> None:
    d = docs(NoArrivals(FIXTURES))
    for path, doc in d.items():
        if path.startswith("stations/") and path != "stations/index.json":
            assert doc["summary"]["measured_status"] == "not_measured"
            assert doc["measured_history"] == []
            assert doc["summary"]["disruption"] is None


def test_station_files_are_compact(tmp_path: Path) -> None:
    _, outputs = export.build_outputs(RecordedMarts(FIXTURES), W46, STAMP, allow_incomplete=True)
    export.write_outputs(tmp_path, outputs)
    text = (tmp_path / "stations" / "940GZZLUOXC.json").read_text()
    assert text.count("\n") == 1 and ": " not in text
    assert json.loads(text)["station"]["naptan_id"] == "940GZZLUOXC"
    # Every other file keeps its indentation.
    assert (tmp_path / "meta" / "index.json").read_text().count("\n") > 10


def test_station_schemas_reject_bad_documents() -> None:
    d = docs(RecordedMarts(FIXTURES))
    detail = json.loads(json.dumps(d["stations/940GZZLUOXC.json"]))
    detail["measured_history"][0]["weeks"][0]["measured_status"] = "good"
    with pytest.raises(ValidationError):
        validate(detail, "station")
    index = json.loads(json.dumps(d["stations/index.json"]))
    del index["stations"][0]["summary"]
    with pytest.raises(ValidationError):
        validate(index, "station_index")
    meta = json.loads(json.dumps(d["meta/index.json"]))
    meta["stations"]["count"] = "many"
    with pytest.raises(ValidationError):
        validate(meta, "index")


def test_station_upload_rules() -> None:
    for key in ("stations/index.json", "stations/940GZZLUOXC.json"):
        assert upload.classify(key) == (upload.JSON, upload.SHORT_CACHE)
    with pytest.raises(upload.UploadError):
        upload.classify("stations/../x.json")


def test_flag_off_writes_no_station_files_and_no_block() -> None:
    d = docs(RecordedMarts(FIXTURES), enabled=False)
    assert not [p for p in d if p.startswith("stations/")]
    assert "stations" not in d["meta/index.json"]
    validate(d["meta/index.json"], "index")


def test_cli_flag_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = ["--week", "2026-W46", "--recorded", str(FIXTURES), "--no-cards", "--allow-incomplete"]
    monkeypatch.delenv(export.STATIONS_ENABLED_VAR, raising=False)
    assert export.main([*args, "--out-dir", str(tmp_path / "off")]) == 0
    assert not (tmp_path / "off" / "stations").exists()
    monkeypatch.setenv(export.STATIONS_ENABLED_VAR, "true")
    assert export.main([*args, "--out-dir", str(tmp_path / "on")]) == 0
    assert (tmp_path / "on" / "stations" / "index.json").exists()


def test_schedule_source() -> None:
    doc = docs(RecordedMarts(FIXTURES))["stations/940GZZLUOXC.json"]
    sources = {
        (h["line_id"], h["direction"]): h["weeks"][0]["schedule_source"]
        for h in doc["measured_history"]
    }
    assert sources[("victoria", "northbound")] == "timetable"
    assert sources[("central", "eastbound")] == "observed_only"


class MissingTables(AthenaMarts):
    def __init__(self, error: str) -> None:
        self.error = error
        self.calls: list[str] = []

    def _run(self, stem: str, start: date, end: date) -> list[dict[str, str | None]]:
        self.calls.append(stem)
        raise RuntimeError(self.error)


def test_missing_station_tables_read_as_empty() -> None:
    marts = MissingTables("query q FAILED: TABLE_NOT_FOUND: Table 'x' does not exist")
    assert marts.stations() == []
    assert marts.station_disruption_weekly(W46) == []
    assert marts.station_measured_weekly(W46) == []


def test_other_station_failures_still_raise() -> None:
    marts = MissingTables("query q FAILED: SYNTAX_ERROR")
    with pytest.raises(RuntimeError):
        marts.stations()


def test_history_window_follows_the_export_week() -> None:
    from mind_the_data.publish.marts import queries

    sql = queries("db", W46, W46)["station_measured"]
    assert "between date '2026-08-17' and date '2026-11-09'" in sql
    assert "current_date" not in sql


class MissingStationMarts(RecordedMarts):
    def stations(self) -> list[StationInfo]:
        return []


def test_league_files_survive_missing_station_marts() -> None:
    d = docs(MissingStationMarts(FIXTURES))
    assert "league/latest.json" in d
    assert d["stations/index.json"]["stations"] == []


def test_terminus_rows_are_withheld_with_their_reason_and_never_read_as_good() -> None:
    from mind_the_data.publish.league import Window
    from mind_the_data.publish.stations import StationRows, station_document

    def row(line: str, direction: str, status: str, reason: str | None) -> StationMeasuredRow:
        withheld = status != "measured"
        return StationMeasuredRow(
            week_start=W46,
            naptan_id="940GZZLUBST",
            line_id=line,
            direction=direction,
            observed_hours=100.0,
            trains_observed=None if withheld else 900,
            trains_per_hour=None if withheld else 9.0,
            median_gap_minutes=None if withheld else 6.5,
            long_gaps_per_100_hours=None if withheld else 1.0,
            scheduled_trains=1200,
            trains_on_boards_share=None if withheld else 0.9,
            excess_wait_minutes=None if withheld else 0.4,
            gate_judged_hours=100,
            gate_pass_share=1.0,
            measured_status=status,
            measured_withheld_reason=reason,
        )

    # A station that ends one line (withheld, terminus) and is a through station
    # on another (measured): each row stands on its own.
    station = StationInfo(
        naptan_id="940GZZLUBST",
        station_name="Baker Street",
        lat=51.52,
        lon=-0.157,
        line_ids=("bakerloo", "metropolitan"),
        modes=("tube",),
        is_measurement_station=True,
    )
    rows = [
        row("bakerloo", "inbound", "measured", None),
        row("metropolitan", "inbound", "withheld", "terminus"),
        row("metropolitan", "outbound", "withheld", "terminus"),
    ]
    window = Window(W46, W46 + timedelta(days=6))
    doc = station_document(station, StationRows(window, [], rows), STAMP)
    by_line = {h["line_id"] + h["direction"]: h["weeks"][0] for h in doc["measured_history"]}
    end = by_line["metropolitaninbound"]
    assert end["measured_status"] == "withheld"
    assert end["measured_withheld_reason"] == "terminus"
    for field in (
        "trains_observed",
        "trains_per_hour",
        "median_gap_minutes",
        "long_gaps_per_100_hours",
        "trains_on_boards_share",
        "excess_wait_minutes",
    ):
        assert end[field] is None
    assert end["scheduled_trains"] == 1200
    assert by_line["bakerlooinbound"]["measured_status"] == "measured"
    assert doc["summary"]["status_reasons"] == ["terminus"]
    assert (doc["summary"]["directions_measured"], doc["summary"]["directions_total"]) == (1, 3)

    only_end = station_document(station, StationRows(window, [], rows[1:]), STAMP)
    assert only_end["summary"]["measured_status"] == "withheld"
    assert only_end["summary"]["status_reasons"] == ["terminus"]
