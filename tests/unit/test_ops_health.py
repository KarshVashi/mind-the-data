import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from mind_the_data.common.raw import Envelope, Feed, RequestInfo, encode_ndjson_gz, object_key
from mind_the_data.ops.health import (
    Alarm,
    Section,
    alarm_section,
    alarms_fired,
    completeness_section,
    current_alarms,
    day_bounds,
    format_age,
    freshness_section,
    guarded,
    industrial_action_section,
    lambda_section,
    newest_object,
    render,
)
from mind_the_data.publish.mechanics import IndustrialAction, load_industrial_action

BUCKET = "mind-the-data-raw-123456789012"


def _completeness(line_cov: float, arr_cov: float) -> str:
    def feed(cov: float, target: float) -> dict[str, Any]:
        return {
            "minutes": 1440,
            "covered": round(1440 * cov),
            "coverage": cov,
            "target": target,
            "objects": 1440,
            "request_success": 1.0,
            "gaps": [["2026-11-05T03:00:00+00:00", "2026-11-05T03:20:00+00:00"]],
        }

    return json.dumps(
        {
            "window_start": "2026-11-05T00:00:00+00:00",
            "window_end": "2026-11-06T00:00:00+00:00",
            "passed": True,
            "feeds": {"line_status": feed(line_cov, 0.99), "arrivals": feed(arr_cov, 0.97)},
        }
    )


def test_completeness_on_target_has_no_problems() -> None:
    section = completeness_section(_completeness(0.995, 0.98))
    assert section.problems == []
    assert any("99.50%" in line for line in section.lines)


def test_completeness_below_target_names_the_feed_and_gap() -> None:
    section = completeness_section(_completeness(0.995, 0.95))
    assert section.problems == [
        "completeness: arrivals 95.00% is below its 97% target (gaps 03:00-03:20)"
    ]


@pytest.mark.parametrize("text", ["", "not json", '{"feeds": 1}', "{}"])
def test_completeness_that_did_not_run_is_a_problem(text: str) -> None:
    # A crashed report must never read as a green day.
    assert completeness_section(text).problems


def test_alarm_states_and_firings() -> None:
    alarms = [Alarm("mind-the-data-a", "OK"), Alarm("mind-the-data-b", "ALARM"), Alarm("c", "OK")]
    section = alarm_section(alarms, {"mind-the-data-a": 2})
    assert section.problems == [
        "alarms: mind-the-data-a went into ALARM 2x",
        "alarms: mind-the-data-b is in ALARM",
    ]
    assert "| mind-the-data-b | ALARM | no |" in section.lines


def test_no_alarms_found_is_a_problem() -> None:
    assert alarm_section([], {}).problems


def test_alarm_section_without_history_shows_a_dash() -> None:
    section = alarm_section([Alarm("mind-the-data-a", "OK")])
    assert "| mind-the-data-a | OK | - |" in section.lines
    assert section.problems == []


def test_insufficient_data_is_not_a_problem() -> None:
    assert alarm_section([Alarm("mind-the-data-r", "INSUFFICIENT_DATA")], {}).problems == []


@mock_aws
def test_current_alarms_reads_only_the_project_prefix() -> None:
    cw = boto3.client("cloudwatch")
    for name in ("mind-the-data-x", "other-y"):
        cw.put_metric_alarm(
            AlarmName=name,
            MetricName="Errors",
            Namespace="AWS/Lambda",
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=1,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
        )
    alarms = current_alarms(cw)
    assert [a.name for a in alarms] == ["mind-the-data-x"]


class FakeHistory:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.kwargs: dict[str, Any] = {}

    def get_paginator(self, name: str) -> "FakeHistory":
        assert name == "describe_alarm_history"
        return self

    def paginate(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        self.kwargs = kwargs
        yield {"AlarmHistoryItems": self.items}


def _transition(name: str, new: str) -> dict[str, Any]:
    data = {"oldState": {"stateValue": "OK"}, "newState": {"stateValue": new}}
    return {"AlarmName": name, "HistoryData": json.dumps(data)}


def test_alarms_fired_counts_transitions_into_alarm() -> None:
    fake = FakeHistory(
        [
            _transition("mind-the-data-a", "ALARM"),
            _transition("mind-the-data-a", "OK"),
            _transition("mind-the-data-a", "ALARM"),
            _transition("other-b", "ALARM"),
            {"AlarmName": "mind-the-data-c", "HistoryData": "garbage"},
        ]
    )
    start, end = day_bounds(date(2026, 11, 5))
    assert alarms_fired(fake, start, end) == {"mind-the-data-a": 2}  # type: ignore[arg-type]
    assert fake.kwargs["StartDate"] == datetime(2026, 11, 5, tzinfo=UTC)
    assert fake.kwargs["EndDate"] == datetime(2026, 11, 6, tzinfo=UTC)


class FakeMetrics:
    def __init__(self, sums: dict[tuple[str, str], float]) -> None:
        self.sums = sums

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Period"] == 86400
        key = (kwargs["Dimensions"][0]["Value"], kwargs["MetricName"])
        return {"Datapoints": [{"Sum": self.sums[key]}] if key in self.sums else []}


def test_lambda_errors_and_throttles_are_problems() -> None:
    fake = FakeMetrics({("p", "Invocations"): 1440, ("p", "Errors"): 3, ("r", "Throttles"): 1})
    start, end = day_bounds(date(2026, 11, 5))
    section = lambda_section(fake, start, end, ["p", "r"])  # type: ignore[arg-type]
    assert "| p | 1440 | 3 | 0 |" in section.lines
    assert section.problems == ["lambda: p had 3 errors", "lambda: r was throttled 1x"]


def test_guarded_turns_an_aws_error_into_a_problem() -> None:
    def denied() -> Section:
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeAlarms")

    section = guarded("Alarms", denied)
    assert section.problems and "AccessDenied" in section.problems[0]


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
        )
        yield client


def _put(s3: Any, feed: Feed, at: datetime) -> None:
    poll_id = uuid4()
    env = Envelope(
        poll_id=poll_id,
        polled_at=at,
        feed=feed,
        request=RequestInfo(path="/x"),
        http_status=200,
        payload=[],
    )
    s3.put_object(Bucket=BUCKET, Key=object_key(feed, at, poll_id), Body=encode_ndjson_gz([env]))


def test_newest_object_walks_the_latest_partition(s3: Any) -> None:
    assert newest_object(s3, BUCKET, "line_status") is None
    _put(s3, Feed.LINE_STATUS, datetime(2026, 11, 4, 23, 59, tzinfo=UTC))
    _put(s3, Feed.LINE_STATUS, datetime(2026, 11, 5, 9, 1, tzinfo=UTC))
    newest = newest_object(s3, BUCKET, "line_status")
    assert newest is not None
    # moto stamps LastModified with the real write time; only presence matters here.
    assert newest.tzinfo is not None


def test_freshness_flags_stale_and_missing_feeds(s3: Any) -> None:
    _put(s3, Feed.LINE_STATUS, datetime(2026, 11, 5, 9, 1, tzinfo=UTC))
    now = datetime.now(UTC)
    fresh = freshness_section(s3, BUCKET, now, ["line_status", "arrivals"])
    assert fresh.problems == ["raw: no arrivals objects at all"]
    stale = freshness_section(s3, BUCKET, now + timedelta(hours=1), ["line_status"])
    assert stale.problems == ["raw: newest line_status object is 60m old"]


@pytest.mark.parametrize(
    ("seconds", "text"), [(59, "59s"), (600, "10m"), (7200, "2h"), (8 * 86400, "8d")]
)
def test_format_age(seconds: int, text: str) -> None:
    assert format_age(timedelta(seconds=seconds)) == text


def test_render_lists_every_problem_up_front() -> None:
    report = render("T", [Section("A", ["x"], ["a broke"]), Section("B", ["y"])], "run link")
    assert report.startswith("## T\n")
    assert "**Missed:**\n\n- a broke" in report
    assert report.rstrip().endswith("run link")
    assert "**All targets met.**" in render("T", [Section("A", ["x"])])


def test_insufficient_data_for_over_an_hour_is_a_problem() -> None:
    now = datetime(2026, 11, 6, 2, 10, tzinfo=UTC)
    fresh = Alarm("mind-the-data-r", "INSUFFICIENT_DATA", now - timedelta(minutes=30))
    stuck = Alarm("mind-the-data-s", "INSUFFICIENT_DATA", now - timedelta(hours=3))
    section = alarm_section([fresh, stuck], {}, now)
    assert section.problems == [
        "alarms: mind-the-data-s has had insufficient data since 2026-11-05 23:10"
    ]


def test_a_poller_that_never_ran_is_a_problem_but_a_quiet_reference_is_not() -> None:
    fake = FakeMetrics({})
    start, end = day_bounds(date(2026, 11, 5))
    functions = ["mind-the-data-poller", "mind-the-data-reference"]
    section = lambda_section(fake, start, end, functions)  # type: ignore[arg-type]
    assert section.problems == ["lambda: mind-the-data-poller was never invoked"]


def _action(action_id: str, end: date, status: str) -> IndustrialAction:
    return IndustrialAction(
        action_id=action_id,
        union_name="RMT",
        applies_to="mode",
        applies_to_id="tube",
        start_london_date=end,
        end_london_date=end,
        status=status,
        description="",
        source_publisher="",
        source_title="",
        source_date=end,
        source_url="https://example.org/",
    )


def test_industrial_action_section_flags_announced_rows_past_the_grace() -> None:
    today = date(2026, 12, 1)
    actions = [
        # Ended 27 November: 4 days ago, past the 3-day grace.
        _action("late", date(2026, 11, 27), "announced"),
        # Ended 28 November: exactly 3 days ago, still inside the grace.
        _action("grace", date(2026, 11, 28), "announced"),
        _action("updated", date(2026, 11, 1), "held"),
    ]
    section = industrial_action_section(actions, today)
    assert len(section.problems) == 1
    assert len(section.lines) == 1 and "`late`" in section.lines[0]
    assert industrial_action_section(actions[1:], today).problems == []


def test_seeded_industrial_action_loads_for_the_report() -> None:
    actions = load_industrial_action(Path(__file__).resolve().parents[2] / "transform")
    # The report reads the checkout's seed; today's announced rows are in the future.
    assert actions and "announced" in {a.status for a in actions}
