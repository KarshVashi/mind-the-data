from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import boto3
from moto import mock_aws

from mind_the_data.common.config import Station, StationRole
from mind_the_data.common.raw import Envelope, Feed, RequestInfo, encode_ndjson_gz, object_key
from mind_the_data.ops.arrivals_check import (
    expected_lines,
    lines_seen,
    naptan_of,
    recent_polls,
    strict_check,
    strict_section,
)

OXC = "940GZZLUOXC"
BNK = "940GZZLUBNK"
BUCKET = "mind-the-data-raw-123456789012"


def _station(naptan: str, line: str) -> Station:
    return Station(line_id=line, naptan_id=naptan, name=naptan, role=StationRole.CORE, reason="x")


def _env(naptan: str, lines: list[str], status: int = 200) -> Envelope:
    payload = [{"naptanId": naptan, "lineId": line} for line in lines]
    # Another station's predictions on the same board must not count.
    payload.append({"naptanId": "940GZZOTHER", "lineId": "elsewhere"})
    return Envelope(
        poll_id=uuid4(),
        polled_at=datetime(2026, 11, 5, 12, tzinfo=UTC),
        feed=Feed.ARRIVALS,
        request=RequestInfo(path=f"/StopPoint/{naptan}/Arrivals"),
        http_status=status,
        error=None if status == 200 else f"http_{status}",
        payload=payload if status == 200 else None,
    )


def test_expected_lines_groups_lines_by_station() -> None:
    stations = [_station(OXC, "central"), _station(OXC, "victoria"), _station(BNK, "central")]
    assert expected_lines(stations) == {OXC: {"central", "victoria"}, BNK: {"central"}}


def test_lines_seen_only_counts_the_station_itself() -> None:
    payload = [
        {"naptanId": OXC, "lineId": "central"},
        {"naptanId": "940GZZOTHER", "lineId": "bakerloo"},
        {"naptanId": OXC},
        "junk",
    ]
    assert lines_seen(payload, OXC) == {"central"}
    assert lines_seen(None, OXC) == set()


def test_naptan_of_reads_arrivals_paths_only() -> None:
    assert naptan_of(f"/StopPoint/{OXC}/Arrivals") == OXC
    assert naptan_of("/Line/Mode/tube/Status") is None


def test_strict_check_unions_polls_and_lists_missing_lines() -> None:
    expected = {OXC: {"central", "victoria", "bakerloo"}, BNK: {"central"}}
    polls = [
        [_env(OXC, ["central"]), _env(BNK, [], status=503)],
        [_env(OXC, ["victoria"]), _env(BNK, ["central"])],
    ]
    result = strict_check(expected, polls)
    assert result.polls == 2
    assert result.missing == {OXC: ["bakerloo"]}
    assert result.failed_requests == {BNK: 1}
    section = strict_section(expected, result, {OXC: "Oxford Circus"})
    assert "3/4 station-line pairs" in section.lines[0]
    assert section.problems == [
        f"arrivals: Oxford Circus ({OXC}) shows no predictions for bakerloo"
    ]


def test_a_station_that_only_failed_misses_every_line() -> None:
    result = strict_check({BNK: {"central"}}, [[_env(BNK, [], status=429)]])
    assert result.missing == {BNK: ["central"]}


def test_no_polls_is_a_problem_not_a_pass() -> None:
    result = strict_check({OXC: {"central"}}, [])
    assert strict_section({OXC: {"central"}}, result, {}).problems == [
        "arrivals: no arrivals objects in the window"
    ]


@mock_aws
def test_recent_polls_reads_only_the_window() -> None:
    s3: Any = boto3.client("s3")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    now = datetime(2026, 11, 5, 12, 5, 30, tzinfo=UTC)
    # 11:49 is outside a 15 minute window ending 12:05; 11:50 to 12:04 are inside.
    for minutes_ago in (16, 15, 6, 1, 0):
        at = now.replace(second=3) - timedelta(minutes=minutes_ago)
        poll_id = uuid4()
        env = _env(OXC, ["central"]).model_copy(update={"poll_id": poll_id, "polled_at": at})
        s3.put_object(
            Bucket=BUCKET,
            Key=object_key(Feed.ARRIVALS, at, poll_id),
            Body=encode_ndjson_gz([env]),
        )
    polls = recent_polls(s3, BUCKET, now)
    assert len(polls) == 3


@mock_aws
def test_recent_polls_spans_an_hour_boundary() -> None:
    s3: Any = boto3.client("s3")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    now = datetime(2026, 11, 5, 12, 5, 30, tzinfo=UTC)
    # 11:52 (the previous hour's partition) and 12:03 are both in the window.
    for at in (
        datetime(2026, 11, 5, 11, 52, 3, tzinfo=UTC),
        datetime(2026, 11, 5, 12, 3, 3, tzinfo=UTC),
    ):
        poll_id = uuid4()
        env = _env(OXC, ["central"]).model_copy(update={"poll_id": poll_id, "polled_at": at})
        s3.put_object(
            Bucket=BUCKET,
            Key=object_key(Feed.ARRIVALS, at, poll_id),
            Body=encode_ndjson_gz([env]),
        )
    assert len(recent_polls(s3, BUCKET, now)) == 2
