"""``make status``: one read-only screen answering "is everything healthy?".

GitHub parts go through ``gh``; AWS parts use the read-only ``mtd-dev``
profile. Every part degrades on its own: missing credentials, an expired SSO
session or no ``gh`` login print a one-line reason instead of a traceback.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from mind_the_data.ops.github import Gh, GhError, gh_json
from mind_the_data.ops.health import (
    AWS_ERRORS,
    Section,
    alarm_section,
    current_alarms,
    freshness_section,
)

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_s3 import S3Client

OPS_LABELS = ("ops-health", "ops-daytime")
AWS_TITLES = ("Alarms", "Newest raw object per feed")
_PASS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAIL = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def check_state(rollup: Sequence[dict[str, Any]]) -> str:
    """Collapse a PR's statusCheckRollup into one word, with counts."""
    if not rollup:
        return "no checks"
    passed = failed = pending = 0
    for check in rollup:
        # Check runs carry status/conclusion; commit statuses carry state.
        outcome = (check.get("conclusion") or check.get("state") or "").upper()
        if outcome in _PASS:
            passed += 1
        elif outcome in _FAIL:
            failed += 1
        else:
            pending += 1
    word = "failing" if failed else "pending" if pending else "passing"
    return f"{word} ({passed} ok, {failed} failed, {pending} pending)"


def pr_section(gh: Gh) -> Section:
    section = Section("Open pull requests")
    prs = gh_json(
        gh,
        [
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,isDraft,statusCheckRollup",
        ],
    )
    if not prs:
        section.lines.append("none")
    for pr in prs or []:
        draft = " [draft]" if pr.get("isDraft") else ""
        state = check_state(pr.get("statusCheckRollup") or [])
        section.lines.append(
            f"- #{pr['number']} {pr['title']}{draft} ({pr['headRefName']}): {state}"
        )
        if state.startswith("failing"):
            section.problems.append(f"PR #{pr['number']} has failing checks")
    return section


def latest_runs(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest run of each workflow, by name."""
    newest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run["workflowName"]
        if name not in newest or run["createdAt"] > newest[name]["createdAt"]:
            newest[name] = run
    return [newest[name] for name in sorted(newest)]


def runs_section(gh: Gh) -> Section:
    section = Section("Last run of each workflow")
    runs = gh_json(
        gh,
        [
            "run",
            "list",
            "--limit",
            "100",
            "--json",
            "workflowName,status,conclusion,createdAt,event,headBranch,url",
        ],
    )
    for run in latest_runs(runs or []):
        result = run.get("conclusion") or run.get("status") or "?"
        section.lines.append(
            f"- {run['workflowName']}: {result} ({run['event']} on {run['headBranch']}, "
            f"{run['createdAt']}) {run['url']}"
        )
        if result in {"failure", "timed_out", "startup_failure"}:
            section.problems.append(f"workflow {run['workflowName']} last run: {result}")
    if not section.lines:
        section.lines.append("no runs")
    return section


def issues_section(gh: Gh, labels: Sequence[str] = OPS_LABELS) -> Section:
    section = Section("Open ops issues")
    for label in labels:
        issues = gh_json(
            gh, ["issue", "list", "--label", label, "--state", "open", "--json", "number,title,url"]
        )
        for issue in issues or []:
            section.lines.append(f"- [{label}] #{issue['number']} {issue['title']} {issue['url']}")
            section.problems.append(f"open {label} issue #{issue['number']}")
    if not section.lines:
        section.lines.append("none")
    return section


def gh_part(title: str, build: Callable[[], Section]) -> Section:
    try:
        return build()
    except GhError as exc:
        return Section(title, [f"unavailable: {exc}"], unavailable=True)


def aws_part(title: str, build: Callable[[], Section]) -> Section:
    try:
        return build()
    except AWS_ERRORS as exc:
        reason = f"unavailable without AWS access ({type(exc).__name__})"
        return Section(title, [reason], unavailable=True)


def collect(
    gh: Gh,
    cloudwatch: "Callable[[], CloudWatchClient]",
    s3: "Callable[[], S3Client]",
    bucket: Callable[[], str],
    now: datetime,
) -> list[Section]:
    """Every section of the status screen; AWS clients are built lazily."""
    return [
        gh_part("Open pull requests", lambda: pr_section(gh)),
        gh_part("Last run of each workflow", lambda: runs_section(gh)),
        aws_part("Alarms", lambda: alarm_section(current_alarms(cloudwatch()), now=now)),
        aws_part("Newest raw object per feed", lambda: freshness_section(s3(), bucket(), now)),
        gh_part("Open ops issues", lambda: issues_section(gh)),
    ]


def render_text(sections: Sequence[Section]) -> str:
    problems = [p for s in sections for p in s.problems]
    missing = [s.title for s in sections if s.unavailable]
    out = [s.markdown() for s in sections]
    if problems:
        out.append("Needs attention:\n" + "\n".join(f"- {p}" for p in problems))
    elif missing:
        out.append("Nothing wrong in what could be read; not checked: " + ", ".join(missing))
    else:
        out.append("All clear.")
    if any(s.unavailable for s in sections if s.title in AWS_TITLES):
        out.append("For the AWS parts: aws sso login --sso-session mtd")
    return "\n".join(out)
