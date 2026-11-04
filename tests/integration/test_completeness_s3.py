import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import boto3
import pytest
from moto import mock_aws

from mind_the_data.common.raw import (
    Envelope,
    Feed,
    RequestInfo,
    encode_ndjson_gz,
    object_key,
)

BUCKET = "mind-the-data-raw-123456789012"
END = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "completeness.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("completeness_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _put(feed: Feed, minute: datetime, oks: list[bool]) -> None:
    poll_id = uuid4()
    polled_at = minute + timedelta(seconds=2)
    envelopes = [
        Envelope(
            poll_id=poll_id,
            polled_at=polled_at,
            feed=feed,
            request=RequestInfo(path=f"/r/{i}"),
            http_status=200 if ok else 503,
            error=None if ok else "http_503",
            payload=[] if ok else None,
        )
        for i, ok in enumerate(oks)
    ]
    boto3.client("s3").put_object(
        Bucket=BUCKET,
        Key=object_key(feed, polled_at, poll_id),
        Body=encode_ndjson_gz(envelopes),
    )


@pytest.fixture
def bucket() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3").create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
        )
        yield


def test_script_reports_coverage_and_fails_below_target(
    bucket: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # Last 60 minutes before 09:00. Status misses 08:10; arrivals has 08:20 with
    # only 8/10 stations ok (below the 90% share) and no object at 08:30.
    for i in range(60):
        minute = END - timedelta(minutes=60 - i)
        if minute.minute != 10:
            _put(Feed.LINE_STATUS, minute, [True])
        if minute.minute == 20:
            _put(Feed.ARRIVALS, minute, [True] * 8 + [False] * 2)
        elif minute.minute != 30:
            _put(Feed.ARRIVALS, minute, [True] * 10)

    code = _script().main(["--hours", "1", "--end", END.isoformat(), "--bucket", BUCKET, "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["passed"] is False
    status, arrivals = report["feeds"]["line_status"], report["feeds"]["arrivals"]
    assert (status["covered"], status["minutes"]) == (59, 60)
    assert status["gaps"] == [["2026-11-08T08:10:00+00:00", "2026-11-08T08:10:00+00:00"]]
    assert (arrivals["covered"], arrivals["objects"]) == (58, 59)
    assert arrivals["request_success"] == round(588 / 590, 4)


def test_script_passes_when_every_minute_is_covered(
    bucket: None, capsys: pytest.CaptureFixture[str]
) -> None:
    for i in range(30):
        minute = END - timedelta(minutes=30 - i)
        _put(Feed.LINE_STATUS, minute, [True])
        _put(Feed.ARRIVALS, minute, [True] * 10)

    code = _script().main(["--hours", "0.5", "--end", END.isoformat(), "--bucket", BUCKET])

    out = capsys.readouterr().out
    assert code == 0
    assert out.strip().endswith("PASS")
    assert "30/30" in out
