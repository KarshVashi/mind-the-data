"""Published documents against their JSON Schemas, from recorded mart results."""

import copy
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from mind_the_data.publish import export
from mind_the_data.publish.documents import (
    Stamp,
    dbt_vars,
    league_document,
    line_document,
    typical_bad_hours,
)
from mind_the_data.publish.insight import pick_insight
from mind_the_data.publish.league import (
    PROVISIONAL_COMPLETENESS,
    MeasuredView,
    Window,
    league_from_daily,
)
from mind_the_data.publish.marts import BadHourRow, RecordedMarts, WeeklyRow
from mind_the_data.publish.schemas import SCHEMA_NAMES, load_schema, validate

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "marts"
STAMP = Stamp(generated_at=datetime(2026, 11, 9, 6, 30, tzinfo=UTC), git_sha="0123abc")
WEEK = date(2026, 11, 2)


@pytest.fixture
def marts() -> RecordedMarts:
    return RecordedMarts(FIXTURES)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schemas_are_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


def test_every_output_validates(marts: RecordedMarts) -> None:
    _league, outputs = export.build_outputs(marts, WEEK, STAMP, allow_incomplete=True)
    paths = {o.path for o in outputs}
    assert {"league/2026-W45.json", "meta/methodology.json", "meta/index.json"} <= paths
    # A partial week is never written as latest.
    assert "league/latest.json" not in paths
    assert len([p for p in paths if p.startswith("lines/")]) == 20
    for output in outputs:
        validate(output.document, output.schema)
        doc = output.document
        assert doc["schema_version"] and doc["git_sha"] == "0123abc"
        assert doc["generated_at"] == "2026-11-09T06:30:00Z"
        assert doc["data_window"]["timezone"] == "Europe/London"
    league_doc = next(o.document for o in outputs if o.path == "league/2026-W45.json")
    assert league_doc["data_window"] == {
        "start": "2026-11-02",
        "end": "2026-11-08",
        "timezone": "Europe/London",
        "iso_week": "2026-W45",
    }
    assert league_doc["views"] == {"declared": "published", "measured": "not_measured"}
    assert all(line["measured"] is None for line in league_doc["lines"])
    assert all(line["measured_status"] == "not_measured" for line in league_doc["lines"])
    assert league_doc["worst_incident"]["line_id"] == "district"
    assert league_doc["worst_incident"]["duration_minutes"] == 1051


def test_build_is_deterministic(marts: RecordedMarts) -> None:
    _, first = export.build_outputs(marts, WEEK, STAMP, allow_incomplete=True)
    _, second = export.build_outputs(marts, WEEK, STAMP, allow_incomplete=True)
    assert [o.document for o in first] == [o.document for o in second]


def _league_doc(marts: RecordedMarts) -> dict[str, Any]:
    window = Window(date(2026, 11, 4), date(2026, 11, 8))
    league = league_from_daily(
        window,
        marts.lines(),
        marts.daily(window.start, window.end),
        marts.incidents(window.start, window.end),
        marts.latest_minute_london(),
    )
    return league_document(league, pick_insight(league), STAMP)


def test_custom_window_document(marts: RecordedMarts) -> None:
    doc = _league_doc(marts)
    validate(doc, "league")
    assert doc["data_window"]["iso_week"] is None
    suffragette = next(line for line in doc["lines"] if line["line_id"] == "suffragette")
    assert suffragette["is_provisional"]
    assert suffragette["provisional_reasons"][0]["decisions"] == ["D22", "D35"]
    assert doc["insight"]["kind"] == "lowest_good_service"
    assert doc["is_window_complete"] is False
    assert doc["data_through"] == "2026-11-08T18:21:00"
    # Every decision a flag cites is listed as open.
    cited = {
        d for line in doc["lines"] for r in line["provisional_reasons"] for d in r["decisions"]
    }
    assert cited and cited <= set(doc["open_decisions"])
    assert doc["worst_incident"]["started_at_london"] == "2026-11-07T07:49:00"


def test_measured_view_validates_when_present(marts: RecordedMarts) -> None:
    window = Window(date(2026, 11, 4), date(2026, 11, 8))
    league = league_from_daily(window, marts.lines(), marts.daily(window.start, window.end), [])
    measured = MeasuredView(
        excess_wait_minutes=-0.4,
        long_gaps_per_100_hours=3.2,
        honesty_gap_hours=7.0,
        honesty_gap_share=0.08,
        experienced_rank=2,
    )
    partly = MeasuredView(None, None, None, None, None)
    standings = list(league.standings)
    standings[0] = replace(standings[0], measured=measured)
    standings[1] = replace(standings[1], measured=partly)
    league = replace(league, standings=tuple(standings))
    insight = pick_insight(league)
    assert insight is not None and insight.kind == "honesty_gap"
    doc = league_document(league, insight, STAMP)
    validate(doc, "league")
    assert doc["views"]["measured"] == "published"
    assert doc["lines"][0]["measured_status"] == "measured"
    assert doc["lines"][2]["measured_status"] == "not_measured"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.pop("git_sha"), "git_sha"),
        (lambda d: d["data_window"].update(timezone="UTC"), "Europe/London"),
        (lambda d: d["lines"][0].update(good_service_pct=101), "101"),
        (lambda d: d["lines"][0].update(rank=0), "0"),
        (lambda d: d["lines"][0].update(colour="red"), "red"),
        (lambda d: d["lines"][0].update(measured={"honesty_gap_hours": 3}), "not valid"),
        (lambda d: d["lines"][0].update(extra=1), "extra"),
        (lambda d: d.update(generated_at="2026-11-09 06:30"), "2026-11-09 06:30"),
        (lambda d: d.update(completeness=1.2), "1.2"),
    ],
)
def test_contract_rejects_bad_documents(marts: RecordedMarts, mutate: Any, message: str) -> None:
    doc = copy.deepcopy(_league_doc(marts))
    mutate(doc)
    with pytest.raises(ValidationError, match=message):
        validate(doc, "league")


def test_line_document_history_and_worst_week(marts: RecordedMarts) -> None:
    lines = {line.line_id: line for line in marts.lines()}
    weekly = marts.weekly()
    doc = line_document(lines["central"], weekly, marts.bad_hours(WEEK, WEEK), STAMP)
    assert doc is not None
    validate(doc, "line")
    weeks = [w["week_start"] for w in doc["history"]]
    assert weeks == sorted(weeks)
    # No week in the recording had finished when it was taken.
    assert doc["worst_week"] is None
    assert 0 < len(doc["typical_bad_hours"]) <= 3
    assert line_document(lines["central"], [], [], STAMP) is None


def test_typical_bad_hours_ranks_by_share() -> None:
    rows = [
        BadHourRow("a", 8, 1000, 500),
        BadHourRow("a", 9, 1000, 500),  # same share: the earlier hour first
        BadHourRow("a", 17, 300, 270),
        BadHourRow("a", 13, 15, 15),  # too few scored minutes to count
        BadHourRow("a", 3, 0, 0),
        BadHourRow("a", 12, 1000, 0),
    ]
    assert [h["london_hour"] for h in typical_bad_hours(rows)] == [17, 8, 9]
    assert typical_bad_hours(rows)[0]["disrupted_share"] == 0.9


def test_methodology_thresholds_follow_dbt(marts: RecordedMarts) -> None:
    variables = dbt_vars(ROOT / "transform")
    assert float(variables["provisional_completeness"]) == PROVISIONAL_COMPLETENESS
    _, outputs = export.build_outputs(marts, WEEK, STAMP, allow_incomplete=True)
    doc = next(o.document for o in outputs if o.path == "meta/methodology.json")
    codes = {w["severity_code"] for w in doc["severity_weights"]}
    assert {10, 20} <= codes
    assert doc["thresholds"]["incident_merge_gap_minutes"] == int(
        variables["incident_merge_gap_minutes"]
    )


def test_export_cli_writes_and_dry_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--week", "2026-W45", "--recorded", str(FIXTURES), "--no-cards", "--allow-incomplete"]
    assert export.main([*args, "--dry-run", "--out-dir", str(tmp_path)]) == 0
    assert "would write league/2026-W45.json" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())

    assert export.main([*args, "--out-dir", str(tmp_path)]) == 0
    week = json.loads((tmp_path / "league" / "2026-W45.json").read_text())
    validate(week, "league")
    assert not (tmp_path / "league" / "latest.json").exists()
    assert (tmp_path / "lines" / "victoria.json").exists()


def test_export_refuses_a_non_dev_workgroup() -> None:
    with pytest.raises(SystemExit):
        export.main(["--recorded", str(FIXTURES), "--workgroup", "mind-the-data-prod"])


def test_latest_complete_week_needs_data(marts: RecordedMarts) -> None:
    # 2026-W44 has finished but holds nothing observed: the collector began on 4 November.
    assert export.latest_complete_week(marts.weekly()) is None
    with pytest.raises(SystemExit, match="no complete week"):
        export.main(["--recorded", str(FIXTURES), "--dry-run", "--no-cards"])


class FinishedWeek(RecordedMarts):
    """The recording, as if W45 had finished and the data had moved on."""

    def weekly(self) -> list[WeeklyRow]:
        return [replace(w, is_week_complete=True) for w in super().weekly()]

    def latest_minute_london(self) -> datetime | None:
        return datetime(2026, 11, 9, 0, 5)


def test_incomplete_week_is_refused(marts: RecordedMarts) -> None:
    with pytest.raises(export.IncompleteWeek, match="2026-W45 is not complete"):
        export.build_outputs(marts, WEEK, STAMP)
    with pytest.raises(SystemExit, match="allow-incomplete"):
        export.main(["--week", "2026-W45", "--recorded", str(FIXTURES), "--dry-run"])


def test_latest_is_only_the_latest_complete_week() -> None:
    finished = FinishedWeek(FIXTURES)
    league, outputs = export.build_outputs(finished, WEEK, STAMP)
    assert league.is_complete
    paths = {o.path for o in outputs}
    assert "league/latest.json" in paths
    latest = next(o.document for o in outputs if o.path == "league/latest.json")
    assert latest["is_window_complete"] is True

    # Wall clock says W45 is over, but the data stops on its last day.
    class Stale(FinishedWeek):
        def latest_minute_london(self) -> datetime | None:
            return datetime(2026, 11, 8, 23, 0)

    with pytest.raises(export.IncompleteWeek):
        export.build_outputs(Stale(FIXTURES), WEEK, STAMP)

    # Asking for an older finished week writes it under its own name; latest
    # stays the newest complete week (D39).
    _, outputs = export.build_outputs(Later(FIXTURES), WEEK, STAMP)
    docs = {o.path: o.document for o in outputs}
    assert docs["league/latest.json"]["data_window"]["iso_week"] == "2026-W46"
    assert docs["league/2026-W45.json"]["data_window"]["iso_week"] == "2026-W45"


class Later(FinishedWeek):
    """W45 and a copy of it as W46, both finished, with data reaching W47."""

    def weekly(self) -> list[WeeklyRow]:
        rows = super().weekly()
        return rows + [replace(w, week_start=date(2026, 11, 9)) for w in rows]

    def latest_minute_london(self) -> datetime | None:
        return datetime(2026, 11, 16, 0, 5)


def test_index_lists_the_archive_and_the_lines() -> None:
    # Asking for the newest week still writes every complete week before it.
    _, outputs = export.build_outputs(Later(FIXTURES), date(2026, 11, 9), STAMP)
    docs = {o.path: o.document for o in outputs}
    index = docs["meta/index.json"]
    validate(index, "index")
    assert index["latest_week"] == "2026-W46"
    assert [w["iso_week"] for w in index["weeks"]] == ["2026-W46", "2026-W45"]
    # Every listed file is written in the same run, and nothing archived is unlisted.
    listed = {w["path"] for w in index["weeks"]} | {line["path"] for line in index["lines"]}
    assert listed <= set(docs)
    assert {p for p in docs if p.startswith("league/2")} == {w["path"] for w in index["weeks"]}
    assert index["data_window"] == {
        "start": "2026-11-02",
        "end": "2026-11-15",
        "timezone": "Europe/London",
        "iso_week": None,
    }
    assert index["data_through"] == "2026-11-16T00:05:00"
    names = [line["line_name"] for line in index["lines"]]
    assert names == sorted(names) and len(names) == 20
    victoria = next(line for line in index["lines"] if line["line_id"] == "victoria")
    assert victoria == {
        "line_id": "victoria",
        "line_name": "Victoria",
        "mode": "tube",
        "colour": "#00A3E0",
        "is_low_volume": False,
        "path": "lines/victoria.json",
    }


def test_index_before_the_first_complete_week(marts: RecordedMarts) -> None:
    _, outputs = export.build_outputs(marts, WEEK, STAMP, allow_incomplete=True)
    index = next(o.document for o in outputs if o.path == "meta/index.json")
    validate(index, "index")
    # The partial week is written under its own name but not archived.
    assert index["latest_week"] is None and index["weeks"] == []
    assert index["data_window"]["iso_week"] == "2026-W45"
    assert index["completeness"] is None


def test_git_sha_marks_a_dirty_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "abcdef1")
    assert export.git_sha() == "abcdef1"
    monkeypatch.delenv("GITHUB_SHA")
    sha = export.git_sha()
    assert sha == "unknown" or len(sha.removesuffix("-dirty")) == 40


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_post_snapshot_custom_window(tmp_path: Path) -> None:
    snapshot = _load_script("post_snapshot")
    code = snapshot.main(
        [
            "--start", "2026-11-04", "--end", "2026-11-08",
            "--recorded", str(FIXTURES), "--out-dir", str(tmp_path), "--no-cards",
        ]
    )  # fmt: skip
    assert code == 0
    doc = json.loads((tmp_path / "league.json").read_text())
    validate(doc, "league")
    assert doc["data_window"]["start"] == "2026-11-04"
    sheet = (tmp_path / "numbers.md").read_text()
    assert "| 18 | District | 6,420 | 33.8 |" in sheet
    assert "Suffragette" in sheet and "(D22, D35)" in sheet
