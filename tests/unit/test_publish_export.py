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
    GATE_MAX_OUT_OF_BAND_SHARE,
    MEASURED_VIEW_WITHHELD_LINES,
    MIN_MEASURED_SHARE,
    MIN_OPEN_SHARE_TO_RANK,
    PROVISIONAL_COMPLETENESS,
    TRAINS_RUN_WITHHELD_LINES,
    MeasuredView,
    Window,
    league_from_daily,
)
from mind_the_data.publish.marts import BadHourRow, RecordedMarts, WeeklyRow
from mind_the_data.publish.schemas import SCHEMA_NAMES, load_schema, validate

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "marts"
STAMP = Stamp(generated_at=datetime(2026, 11, 9, 6, 30, tzinfo=UTC), git_sha="0123abc")
# The recording reaches Saturday 14 November: W45 is complete, W46 is not.
WEEK = date(2026, 11, 2)
PARTIAL_WEEK = date(2026, 11, 9)


@pytest.fixture
def marts() -> RecordedMarts:
    return RecordedMarts(FIXTURES)


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schemas_are_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


def test_every_output_validates(marts: RecordedMarts) -> None:
    _league, outputs = export.build_outputs(marts, PARTIAL_WEEK, STAMP, allow_incomplete=True)
    paths = {o.path for o in outputs}
    assert {
        "league/2026-W45.json",
        "league/2026-W46.json",
        "league/latest.json",
        "meta/methodology.json",
        "meta/index.json",
    } <= paths
    # A partial week is never written as latest: latest is the complete W45.
    latest = next(o.document for o in outputs if o.path == "league/latest.json")
    assert latest["data_window"]["iso_week"] == "2026-W45"
    partial = next(o.document for o in outputs if o.path == "league/2026-W46.json")
    assert partial["is_window_complete"] is False
    assert len([p for p in paths if p.startswith("lines/")]) == 20
    for output in outputs:
        validate(output.document, output.schema)
        doc = output.document
        assert doc["schema_version"] and doc["git_sha"] == "0123abc"
        assert doc["generated_at"] == "2026-11-09T06:30:00Z"
        assert doc["data_window"]["timezone"] == "Europe/London"
    league_doc = next(o.document for o in outputs if o.path == "league/2026-W45.json")
    assert league_doc["is_window_complete"] is True
    assert league_doc["data_window"] == {
        "start": "2026-11-02",
        "end": "2026-11-08",
        "timezone": "Europe/London",
        "iso_week": "2026-W45",
    }
    # The measured view is published for line-weeks whose gate passed (D42, D51).
    assert league_doc["views"] == {"declared": "published", "measured": "published"}
    by_id = {line["line_id"]: line for line in league_doc["lines"]}
    for line in league_doc["lines"]:
        assert (line["measured"] is None) == (line["measured_withheld_reason"] is not None)
        reason = line["measured_withheld_reason"]
        assert line["measured_status"] == (
            "measured"
            if reason is None
            else "not_measured"
            if reason == "line_not_measured"
            else "withheld"
        )
    for line_id in ("dlr", "tram", "waterloo-city", "suffragette"):
        assert by_id[line_id]["measured_withheld_reason"] == "line_not_measured"
    for line_id in ("circle", "hammersmith-city"):
        assert by_id[line_id]["measured_withheld_reason"] == "shared_section_misfiled"
    victoria = by_id["victoria"]["measured"]
    assert victoria is not None and not victoria["is_weak_evidence"]
    assert victoria["trains_run_share"] is not None and victoria["experienced_rank"] is not None
    elizabeth = by_id["elizabeth"]["measured"]
    assert elizabeth is not None and elizabeth["is_weak_evidence"]
    assert elizabeth["experienced_rank"] is None
    assert by_id["lioness"]["measured_withheld_reason"] == "gate_failed"
    assert by_id["liberty"]["measured_withheld_reason"] == "gate_not_judged"
    # The contract ties measured to its reason: one or the other, never both or neither.
    broken = copy.deepcopy(league_doc)
    published = next(i for i, x in enumerate(broken["lines"]) if x["measured"] is not None)
    broken["lines"][published]["measured_withheld_reason"] = "gate_failed"
    with pytest.raises(ValidationError):
        validate(broken, "league")
    withheld = next(i for i, x in enumerate(broken["lines"]) if x["measured"] is None)
    broken = copy.deepcopy(league_doc)
    broken["lines"][withheld]["measured_withheld_reason"] = None
    with pytest.raises(ValidationError):
        validate(broken, "league")
    # M6: the most severity-weighted incident (D48), the bottom line (D48),
    # and no Line of the Week, since W44 holds no data (D47).
    assert league_doc["worst_incident"]["line_id"] == "bakerloo"
    assert league_doc["worst_incident"]["duration_minutes"] == 1005
    assert league_doc["worst_incident"]["severity_weighted_minutes"] == 3763
    assert league_doc["highlights"] == {
        "wooden_spoon": {
            "line_id": "bakerloo",
            "line_name": "Bakerloo",
            "rank": 19,
            "disruption_index": 141.5,
            "is_provisional": True,
            "shared_by": 1,
        },
        "line_of_the_week": None,
        "line_of_the_week_null_reason": "no_previous_week",
    }
    # W45 has no Monday with data (the collector began on Wednesday 4th).
    assert league_doc["monday_morning"] == []
    works = league_doc["engineering_works"]
    assert works[0]["line_id"] == "suffragette" and works[0]["is_unranked_for_works"]
    assert [w["engineering_works_hours"] for w in works] == sorted(
        (w["engineering_works_hours"] for w in works), reverse=True
    )
    suffragette = next(x for x in league_doc["lines"] if x["line_id"] == "suffragette")
    assert suffragette["rank"] is None and suffragette["unranked_reason"] == "engineering_works"
    # W46 is the first week with a Monday morning.
    assert partial["monday_morning"] and partial["monday_morning"][0]["monday"] == "2026-11-09"
    assert partial["highlights"]["line_of_the_week_null_reason"] == "previous_week_incomplete"


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
    assert suffragette["provisional_reasons"][0]["decisions"] == ["D35"]
    assert suffragette["rank"] is None and suffragette["unranked_reason"] == "engineering_works"
    assert doc["insight"]["kind"] == "lowest_good_service"
    assert doc["is_window_complete"] is True
    assert doc["data_through"] == "2026-11-14T15:09:00"
    assert doc["highlights"]["line_of_the_week_null_reason"] == "custom_window"
    # Every decision a flag cites is listed as open.
    cited = {
        d for line in doc["lines"] for r in line["provisional_reasons"] for d in r["decisions"]
    }
    assert cited and cited <= set(doc["open_decisions"])
    assert doc["worst_incident"]["started_at_london"] == "2026-11-08T07:15:00"


def test_measured_view_validates_when_present(marts: RecordedMarts) -> None:
    window = Window(date(2026, 11, 4), date(2026, 11, 8))
    league = league_from_daily(window, marts.lines(), marts.daily(window.start, window.end), [])
    measured = MeasuredView(
        excess_wait_minutes=-0.4,
        long_gaps_per_100_hours=3.2,
        honesty_gap_hours=7.0,
        honesty_gap_share=0.08,
        experienced_rank=2,
        trains_run_share=0.86,
        measured_hours=120,
        measured_share=0.7,
        gate_pass_share=0.9,
        good_service_measured_hours=88,
    )
    partly = MeasuredView(None, None, None, None, None, measured_hours=1)
    standings = list(league.standings)
    standings[0] = replace(standings[0], measured=measured, measured_withheld_reason=None)
    standings[1] = replace(standings[1], measured=partly, measured_withheld_reason=None)
    league = replace(league, standings=tuple(standings))
    insight = pick_insight(league)
    assert insight is not None and insight.kind == "honesty_gap"
    doc = league_document(league, insight, STAMP)
    validate(doc, "league")
    assert doc["views"]["measured"] == "published"
    assert doc["lines"][0]["measured"]["trains_run_share"] == 0.86
    assert doc["lines"][0]["measured_status"] == "measured"
    assert doc["lines"][0]["measured_withheld_reason"] is None
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
        (lambda d: d["lines"][0].update(disruption_index=801), "801"),
        (lambda d: d["lines"][0].update(unranked_reason="closed"), "closed"),
        (lambda d: d["highlights"].update(line_of_the_week_null_reason="none"), "none"),
        (lambda d: d.pop("monday_morning"), "monday_morning"),
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
    # W45 is the only complete week, so it is the worst; W46 has not finished.
    assert doc["worst_week"] is not None and doc["worst_week"]["iso_week"] == "2026-W45"
    assert [w["iso_week"] for w in doc["history"]] == ["2026-W45", "2026-W46"]
    assert all("unranked_reason" in w and "engineering_works_hours" in w for w in doc["history"])
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
    assert float(variables["min_open_share_to_rank"]) == MIN_OPEN_SHARE_TO_RANK
    _, outputs = export.build_outputs(marts, WEEK, STAMP)
    doc = next(o.document for o in outputs if o.path == "meta/methodology.json")
    codes = {w["severity_code"] for w in doc["severity_weights"]}
    assert {10, 20} <= codes
    assert doc["thresholds"]["incident_merge_gap_minutes"] == int(
        variables["incident_merge_gap_minutes"]
    )
    assert doc["thresholds"]["min_open_share_to_rank"] == 0.5
    # D19: the index is a time-weighted mean severity x 100, so Suspended (8) tops it.
    assert (doc["disruption_index"]["min"], doc["disruption_index"]["max"]) == (0.0, 800.0)
    assert doc["settled_decisions"] == [
        "D19",
        "D22",
        "D36",
        "D42",
        "D44",
        "D46",
        "D47",
        "D48",
        "D49",
        "D50",
    ]
    assert not set(doc["settled_decisions"]) & set(doc["open_decisions"])
    # The measured view's thresholds and withheld lines mirror the dbt vars.
    assert float(variables["gate_max_out_of_band_share"]) == GATE_MAX_OUT_OF_BAND_SHARE
    assert float(variables["min_measured_share"]) == MIN_MEASURED_SHARE
    assert tuple(variables["measured_view_withheld_lines"]) == MEASURED_VIEW_WITHHELD_LINES
    assert tuple(variables["trains_run_withheld_lines"]) == TRAINS_RUN_WITHHELD_LINES
    assert doc["thresholds"]["gate_max_out_of_band_share"] == 0.3
    assert doc["measured_view_withheld_lines"] == ["circle", "hammersmith-city"]
    assert variables["gate_test_severity"] == "error"  # D44, settled 2026-11-14


def test_export_cli_writes_and_dry_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--week", "2026-W46", "--recorded", str(FIXTURES), "--no-cards", "--allow-incomplete"]
    assert export.main([*args, "--dry-run", "--out-dir", str(tmp_path)]) == 0
    assert "would write league/2026-W46.json" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())

    assert export.main([*args, "--out-dir", str(tmp_path)]) == 0
    week = json.loads((tmp_path / "league" / "2026-W46.json").read_text())
    validate(week, "league")
    assert week["is_window_complete"] is False
    latest = json.loads((tmp_path / "league" / "latest.json").read_text())
    assert latest["data_window"]["iso_week"] == "2026-W45"
    assert (tmp_path / "lines" / "victoria.json").exists()


def test_export_refuses_a_non_dev_workgroup() -> None:
    with pytest.raises(SystemExit):
        export.main(["--recorded", str(FIXTURES), "--workgroup", "mind-the-data-prod"])


class EarlyRecording(RecordedMarts):
    """The recording as it stood on Sunday 8 November at 18:21: no week had finished."""

    def weekly(self) -> list[WeeklyRow]:
        return [
            replace(w, is_week_complete=w.week_start < WEEK)
            for w in super().weekly()
            if w.week_start <= WEEK
        ]

    def latest_minute_london(self) -> datetime | None:
        return datetime(2026, 11, 8, 18, 21)


def test_latest_complete_week_needs_data(
    marts: RecordedMarts, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert export.latest_complete_week(marts.weekly()) == WEEK
    # 2026-W44 has finished but holds nothing observed: the collector began on 4 November.
    assert export.latest_complete_week(EarlyRecording(FIXTURES).weekly()) is None
    monkeypatch.setattr(export, "marts_from_args", lambda args: EarlyRecording(FIXTURES))
    with pytest.raises(SystemExit, match="no complete week"):
        export.main(["--recorded", str(FIXTURES), "--dry-run", "--no-cards"])


def test_incomplete_week_is_refused(marts: RecordedMarts) -> None:
    with pytest.raises(export.IncompleteWeek, match="2026-W46 is not complete"):
        export.build_outputs(marts, PARTIAL_WEEK, STAMP)
    with pytest.raises(SystemExit, match="allow-incomplete"):
        export.main(["--week", "2026-W46", "--recorded", str(FIXTURES), "--dry-run"])


def test_latest_is_only_the_latest_complete_week(marts: RecordedMarts) -> None:
    league, outputs = export.build_outputs(marts, WEEK, STAMP)
    assert league.is_complete
    paths = {o.path for o in outputs}
    assert "league/latest.json" in paths
    latest = next(o.document for o in outputs if o.path == "league/latest.json")
    assert latest["is_window_complete"] is True

    # Wall clock says W45 is over, but the data stops on its last day.
    class Stale(RecordedMarts):
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


class Later(RecordedMarts):
    """W45 and a copy of it as W46, both finished, with data reaching W47."""

    def weekly(self) -> list[WeeklyRow]:
        rows = [w for w in super().weekly() if w.week_start <= WEEK]
        return rows + [replace(w, week_start=PARTIAL_WEEK) for w in rows if w.week_start == WEEK]

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


def test_index_before_the_first_complete_week() -> None:
    early = EarlyRecording(FIXTURES)
    _, outputs = export.build_outputs(early, WEEK, STAMP, allow_incomplete=True)
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
            "--start", "2026-11-04", "--end", "2026-11-10",
            "--recorded", str(FIXTURES), "--out-dir", str(tmp_path), "--no-cards",
        ]
    )  # fmt: skip
    assert code == 0
    doc = json.loads((tmp_path / "league.json").read_text())
    validate(doc, "league")
    assert doc["data_window"]["start"] == "2026-11-04"
    # Monday 9 November is the window's only Monday.
    assert {m["monday"] for m in doc["monday_morning"]} == {"2026-11-09"}
    sheet = (tmp_path / "numbers.md").read_text()
    assert "| 19 | District | 106 | 32.9 |" in sheet
    assert "| - | Suffragette |" in sheet and "not ranked: engineering works (D22)" in sheet
    assert "## Monday Morning Index (D49, 07:00 to 10:00)" in sheet


def test_only_the_publish_flag_reaches_the_prod_workgroup() -> None:
    assert export.workgroup_for("mind-the-data-dev", publish=False) == "mind-the-data-dev"
    assert export.workgroup_for("mind-the-data-dev", publish=True) == export.PUBLISH_WORKGROUP
    with pytest.raises(ValueError, match="dev workgroup"):
        export.workgroup_for("mind-the-data-prod", publish=False)
