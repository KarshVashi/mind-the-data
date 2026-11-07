import json
from pathlib import Path
from typing import Any

import pytest

from mind_the_data.ops.automerge import main, path_allowed, reasons_not_to_merge

SHA = "a" * 40
FILES = [
    {"filename": "transform/models/staging/stg_x.sql", "status": "modified"},
    {"filename": "tests/unit/t.py", "status": "added"},
]


def _pr(**overrides: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "author": {"login": "KarshVashi"},
        "isCrossRepository": False,
        "isDraft": False,
        "headRefOid": SHA,
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
    assert reasons_not_to_merge(_pr(), FILES, "karshvashi", SHA) == []


def test_a_rename_is_judged_on_both_ends() -> None:
    moved = [
        {
            "filename": "docs/infra.yml",
            "previous_filename": ".github/workflows/infra.yml",
            "status": "renamed",
        }
    ]
    reasons = reasons_not_to_merge(_pr(), moved, "KarshVashi", SHA)
    assert reasons == ["paths outside the allow-list: .github/workflows/infra.yml"]


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([], "no changed files"),
        ([{"filename": "docs/adr/x.md"}], "docs/adr/x.md"),
        ([{"filename": "docs/x.md"}] * 301, "too many files"),
    ],
)
def test_file_rules_block(files: list[dict[str, Any]], reason: str) -> None:
    reasons = reasons_not_to_merge(_pr(), files, "KarshVashi", SHA)
    assert any(reason in r for r in reasons), reasons


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"author": {"login": "someone"}}, "not the repository owner"),
        ({"isCrossRepository": True}, "another repository"),
        ({"isDraft": True}, "draft"),
        ({"headRefOid": "b" * 40}, "moved after ci ran"),
        (
            {"statusCheckRollup": [{"name": "plan", "status": "IN_PROGRESS", "conclusion": ""}]},
            "check plan is pending",
        ),
        ({"statusCheckRollup": [{"context": "x", "state": "FAILURE"}]}, "check x is FAILURE"),
    ],
)
def test_each_rule_blocks(overrides: dict[str, Any], reason: str) -> None:
    reasons = reasons_not_to_merge(_pr(**overrides), FILES, "KarshVashi", SHA)
    assert any(reason in r for r in reasons), reasons


def test_missing_fields_fail_closed() -> None:
    assert len(reasons_not_to_merge({}, [], "KarshVashi", SHA)) >= 5


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = tmp_path / "files.jsonl"
    files.write_text("".join(json.dumps(f) + "\n" for f in FILES))
    pr = tmp_path / "pr.json"
    args = ["--owner", "KarshVashi", "--sha", SHA, "--pr", str(pr), "--files", str(files)]
    pr.write_text(json.dumps(_pr()))
    assert main(args) == 0
    pr.write_text(json.dumps(_pr(isDraft=True)))
    assert main(args) == 1
    assert "not merging: pull request is a draft" in capsys.readouterr().out
