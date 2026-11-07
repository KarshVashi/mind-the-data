import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import NoCredentialsError, ProfileNotFound
from moto import mock_aws

from mind_the_data.ops.github import GhError
from mind_the_data.ops.status import (
    check_state,
    collect,
    latest_runs,
    render_text,
)

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
BUCKET = "mind-the-data-raw-123456789012"

PRS = [
    {
        "number": 17,
        "title": "fix(infra): x",
        "headRefName": "fix/x",
        "isDraft": False,
        "statusCheckRollup": [
            {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "plan", "status": "COMPLETED", "conclusion": "FAILURE"},
        ],
    }
]
RUNS = [
    {
        "workflowName": "ci",
        "status": "completed",
        "conclusion": "success",
        "createdAt": "2026-11-06T03:00:00Z",
        "event": "push",
        "headBranch": "main",
        "url": "u1",
    },
    {
        "workflowName": "ci",
        "status": "completed",
        "conclusion": "failure",
        "createdAt": "2026-11-05T03:00:00Z",
        "event": "push",
        "headBranch": "main",
        "url": "u0",
    },
]


def fake_gh(args: Sequence[str], stdin: str | None) -> str:
    if args[:2] == ["pr", "list"]:
        return json.dumps(PRS)
    if args[:2] == ["run", "list"]:
        return json.dumps(RUNS)
    if args[:2] == ["issue", "list"]:
        label = args[args.index("--label") + 1]
        issues = [{"number": 20, "title": "health", "url": "i"}] if label == "ops-health" else []
        return json.dumps(issues)
    raise AssertionError(args)


def no_gh(args: Sequence[str], stdin: str | None) -> str:
    raise GhError("gh: not found")


def test_check_state_counts() -> None:
    assert check_state([]) == "no checks"
    assert check_state(PRS[0]["statusCheckRollup"]).startswith("failing (1 ok, 1 failed")  # type: ignore[arg-type]
    assert check_state([{"status": "IN_PROGRESS", "conclusion": ""}]).startswith("pending")
    assert check_state([{"state": "SUCCESS"}]).startswith("passing")


def test_latest_runs_keeps_the_newest_per_workflow() -> None:
    assert [r["url"] for r in latest_runs(RUNS)] == ["u1"]


def _no_aws() -> Any:
    raise NoCredentialsError()


def test_everything_degrades_without_access() -> None:
    def no_profile() -> Any:
        raise ProfileNotFound(profile="mtd-dev")

    sections = collect(no_gh, _no_aws, no_profile, lambda: BUCKET, NOW)
    assert all(s.unavailable for s in sections)
    text = render_text(sections)
    assert "unavailable" in text
    assert "All clear" not in text
    assert "aws sso login" in text


@mock_aws
def test_full_screen_with_moto_and_fake_gh() -> None:
    s3: Any = boto3.client("s3")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    cw = boto3.client("cloudwatch")
    cw.put_metric_alarm(
        AlarmName="mind-the-data-poller-error-rate",
        MetricName="Errors",
        Namespace="AWS/Lambda",
        Statistic="Sum",
        Period=60,
        EvaluationPeriods=1,
        Threshold=1,
        ComparisonOperator="GreaterThanOrEqualToThreshold",
    )
    sections = collect(fake_gh, lambda: cw, lambda: s3, lambda: BUCKET, NOW)
    text = render_text(sections)
    assert "#17 fix(infra): x (fix/x): failing" in text
    assert "- ci: success (push on main" in text
    assert "mind-the-data-poller-error-rate" in text
    assert "raw: no line_status objects at all" in text
    assert "[ops-health] #20 health" in text
    assert "Needs attention:" in text
    assert not any(s.unavailable for s in sections)
