from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import boto3
from moto import mock_aws

from mind_the_data.common.raw import Envelope, Feed, RequestInfo, encode_ndjson_gz, object_key
from mind_the_data.quality.completeness import load_feed, window_minutes

BUCKET = "mind-the-data-raw-123456789012"


def _put(s3: Any, at: datetime, marker: str) -> None:
    poll_id = uuid4()
    env = Envelope(
        poll_id=poll_id,
        polled_at=at,
        feed=Feed.LINE_STATUS,
        request=RequestInfo(path=f"/{marker}"),
        http_status=200,
        payload=[],
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=object_key(Feed.LINE_STATUS, at, poll_id),
        Body=encode_ndjson_gz([env]),
    )


def _shape(found: dict[datetime, list[list[Envelope]]]) -> dict[datetime, list[str]]:
    return {m: sorted(o[0].request.path for o in objs) for m, objs in found.items()}


@mock_aws
def test_concurrent_reads_group_objects_by_minute_like_serial_reads() -> None:
    s3: Any = boto3.client("s3")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    minutes = window_minutes(datetime(2026, 11, 5, 13, 0, tzinfo=UTC), 2)
    sampled = minutes[::7]
    for i, minute in enumerate(sampled):
        _put(s3, minute + timedelta(seconds=2), f"a{i}")
        if i % 3 == 0:
            # A retried poll: two objects in the same minute.
            _put(s3, minute + timedelta(seconds=40), f"b{i}")

    serial = load_feed(s3, BUCKET, Feed.LINE_STATUS, minutes, workers=1)
    parallel = load_feed(s3, BUCKET, Feed.LINE_STATUS, minutes, workers=8)
    assert _shape(parallel) == _shape(serial)
    assert len(serial) == len(sampled)
    assert sum(len(v) for v in serial.values()) == len(sampled) + len(sampled[::3])
