import json
import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from mind_the_data.ops.github import GhError, run_gh, sync_issue


class FakeGh:
    """Records gh calls; answers ``issue list`` with the given open issues."""

    def __init__(self, open_issues: list[int]) -> None:
        self.open_issues = open_issues
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: Sequence[str], stdin: str | None) -> str:
        self.calls.append((list(args), stdin))
        if list(args[:2]) == ["issue", "list"]:
            return json.dumps([{"number": n} for n in self.open_issues])
        if list(args[:2]) == ["issue", "create"]:
            return "https://github.com/o/r/issues/9\n"
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(args[:2]) for args, _ in self.calls]


def test_failing_with_nothing_open_creates_the_label_and_an_issue() -> None:
    gh = FakeGh([])
    done = sync_issue(gh, "ops-health", "Collector health", "body", True, "url")
    assert done == "opened https://github.com/o/r/issues/9"
    assert gh.verbs() == ["issue list", "label create", "issue create"]
    create_args, body = gh.calls[-1]
    assert create_args[create_args.index("--label") + 1] == "ops-health"
    assert body == "body"


def test_failing_again_comments_on_the_open_issue() -> None:
    gh = FakeGh([7])
    assert sync_issue(gh, "ops-health", "t", "new report", True, "url") == "commented on #7"
    assert gh.verbs() == ["issue list", "issue comment"]
    assert gh.calls[-1] == (["issue", "comment", "7", "--body-file", "-"], "new report")


def test_passing_closes_every_open_issue_for_the_label() -> None:
    gh = FakeGh([4, 3])
    assert sync_issue(gh, "ops-health", "t", "b", False, "run-url") == "closed #3, #4"
    closes = [args for args, _ in gh.calls if args[:2] == ["issue", "close"]]
    assert [c[2] for c in closes] == ["3", "4"]
    assert "Back on target: run-url" in closes[0]


def test_passing_with_nothing_open_does_nothing() -> None:
    gh = FakeGh([])
    assert sync_issue(gh, "ops-health", "t", "b", False, "u") == "nothing open"
    assert gh.verbs() == ["issue list"]


def test_run_gh_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "not logged in")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(GhError, match="not logged in"):
        run_gh(["pr", "list"])


def test_run_gh_without_gh_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GhError):
        run_gh(["pr", "list"])
