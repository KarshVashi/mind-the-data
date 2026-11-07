import io
import json
from typing import Any

import pytest

from mind_the_data.ops.automerge import main, path_allowed, reasons_not_to_merge

SHA = "a" * 40


def _pr(**overrides: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "author": {"login": "KarshVashi"},
        "isCrossRepository": False,
        "isDraft": False,
        "headRefOid": SHA,
        "files": [{"path": "transform/models/staging/stg_x.sql"}, {"path": "tests/unit/t.py"}],
        "statusCheckRollup": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    pr.update(overrides)
    return pr


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("transform/models/marts/fct_x.sql", True),
        ("tests/conftest.py", True),
        ("docs/runbook.md", True),
        ("docs/adr/010-x.md", False),
        ("transform/macros/x.sql", False),
        ("transform/dbt_project.yml", False),
        (".github/workflows/ci.yml", False),
        ("infra/envs/prod/main.tf", False),
        ("src/mind_the_data/x.py", False),
        ("docs", False),
        ("Makefile", False),
    ],
)
def test_path_allow_list(path: str, allowed: bool) -> None:
    assert path_allowed(path) is allowed


def test_an_eligible_pr_has_no_reasons() -> None:
    assert reasons_not_to_merge(_pr(), "karshvashi", SHA) == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"author": {"login": "someone"}}, "not the repository owner"),
        ({"isCrossRepository": True}, "another repository"),
        ({"isDraft": True}, "draft"),
        ({"headRefOid": "b" * 40}, "moved after ci ran"),
        ({"files": []}, "no changed files"),
        ({"files": [{"path": "docs/adr/x.md"}]}, "docs/adr/x.md"),
        ({"files": [{"path": "docs/x.md"}] * 100}, "too many files"),
        (
            {"statusCheckRollup": [{"name": "plan", "status": "IN_PROGRESS", "conclusion": ""}]},
            "check plan is pending",
        ),
        ({"statusCheckRollup": [{"context": "x", "state": "FAILURE"}]}, "check x is FAILURE"),
    ],
)
def test_each_rule_blocks(overrides: dict[str, Any], reason: str) -> None:
    reasons = reasons_not_to_merge(_pr(**overrides), "KarshVashi", SHA)
    assert any(reason in r for r in reasons), reasons


def test_missing_fields_fail_closed() -> None:
    assert len(reasons_not_to_merge({}, "KarshVashi", SHA)) >= 4


def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_pr())))
    assert main(["--owner", "KarshVashi", "--sha", SHA]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_pr(isDraft=True))))
    assert main(["--owner", "KarshVashi", "--sha", SHA]) == 1
    assert "not merging: pull request is a draft" in capsys.readouterr().out
