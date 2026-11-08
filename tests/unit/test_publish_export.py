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
from mind_the_data.publish.marts import BadHourRow, RecordedMarts
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
    _league, outputs = export.build_outputs(marts, WEEK, STAMP)
    paths = {o.path for o in outputs}
    assert {"league/2026-W45.json", "league/latest.json", "meta/methodology.json"} <= paths
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
    _, first = export.build_outputs(marts, WEEK, STAMP)
    _, second = export.build_outputs(marts, WEEK, STAMP)
    assert [o.document for o in first] == [o.document for o in second]


def _league_doc(marts: RecordedMarts) -> dict[str, Any]:
    window = Window(date(2026, 11, 4), date(2026, 11, 8))
    league = league_from_daily(
        window,
        marts.lines(),
        marts.daily(window.start, window.end),
        marts.incidents(window.start, window.end),
    )
    return league_document(league, pick_insight(league), STAMP)


def test_custom_window_document(marts: RecordedMarts) -> None:
    doc = _league_doc(marts)
    validate(doc, "league")
    assert doc["data_window"]["iso_week"] is None
    suffragette = next(line for line in doc["lines"] if line["line_id"] == "suffragette")
    assert suffragette["is_provisional"]
    assert suffragette["provisional_reasons"][0]["decision"] == "D22"
    assert doc["insight"]["kind"] == "lowest_good_service"
    assert "D22" in doc["open_decisions"] and "D19" in doc["open_decisions"]


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
        BadHourRow("a", 8, 100, 50),
        BadHourRow("a", 9, 100, 50),  # same share: the earlier hour first
        BadHourRow("a", 17, 10, 9),
        BadHourRow("a", 3, 0, 0),
        BadHourRow("a", 12, 100, 0),
    ]
    assert [h["london_hour"] for h in typical_bad_hours(rows)] == [17, 8, 9]
    assert typical_bad_hours(rows)[0]["disrupted_share"] == 0.9


def test_methodology_thresholds_follow_dbt(marts: RecordedMarts) -> None:
    variables = dbt_vars(ROOT / "transform")
    assert float(variables["provisional_completeness"]) == PROVISIONAL_COMPLETENESS
    _, outputs = export.build_outputs(marts, WEEK, STAMP)
    doc = next(o.document for o in outputs if o.path == "meta/methodology.json")
    codes = {w["severity_code"] for w in doc["severity_weights"]}
    assert {10, 20} <= codes
    assert doc["thresholds"]["incident_merge_gap_minutes"] == int(
        variables["incident_merge_gap_minutes"]
    )


def test_export_cli_writes_and_dry_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--week", "2026-W45", "--recorded", str(FIXTURES), "--no-cards"]
    assert export.main([*args, "--dry-run", "--out-dir", str(tmp_path)]) == 0
    assert "would write league/2026-W45.json" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())

    assert export.main([*args, "--out-dir", str(tmp_path)]) == 0
    latest = json.loads((tmp_path / "league" / "latest.json").read_text())
    validate(latest, "league")
    assert (tmp_path / "lines" / "victoria.json").exists()


def test_export_refuses_a_non_dev_workgroup() -> None:
    with pytest.raises(SystemExit):
        export.main(["--recorded", str(FIXTURES), "--workgroup", "mind-the-data-prod"])


def test_latest_complete_week_needs_one(marts: RecordedMarts) -> None:
    with pytest.raises(SystemExit, match="no complete week"):
        export.latest_complete_week(marts)


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
    assert "Suffragette" in sheet and "(D22)" in sheet
