from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from mind_the_data.common.raw import Envelope, Feed, RequestInfo, decode_ndjson_gz
from mind_the_data.common.writer import LocalRawWriter, S3RawWriter

BUCKET = "mind-the-data-raw-123456789012"
POLL_ID = UUID("5f0c6a52-0d6b-4f5e-9d38-2b1a8a1c7e11")
POLLED_AT = datetime(2026, 11, 8, 8, 15, 0, 123000, tzinfo=UTC)


def _envelopes() -> list[Envelope]:
    ok = Envelope(
        poll_id=POLL_ID,
        polled_at=POLLED_AT,
        feed=Feed.ARRIVALS,
        request=RequestInfo(path="/StopPoint/940GZZLUOXC/Arrivals"),
        http_status=200,
        latency_ms=120,
        payload=[{"vehicleId": "201"}],
    )
    failed = Envelope(
        poll_id=POLL_ID,
        polled_at=POLLED_AT,
        feed=Feed.ARRIVALS,
        request=RequestInfo(path="/StopPoint/940GZZLUBNK/Arrivals"),
        http_status=None,
        latency_ms=10000,
        error="timeout",
    )
    return [ok, failed]


@mock_aws
def test_s3_writer_puts_gzipped_ndjson_at_contract_key() -> None:
    s3 = boto3.client("s3", region_name="eu-west-2")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})

    key = S3RawWriter(s3, BUCKET).write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())

    assert key == (
        "raw/arrivals/dt=2026-11-08/hour=08/"
        "arrivals_20261108T081500Z_5f0c6a52-0d6b-4f5e-9d38-2b1a8a1c7e11.ndjson.gz"
    )
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    assert obj["ContentType"] == "application/x-ndjson"
    assert obj.get("ContentEncoding") is None
    assert decode_ndjson_gz(obj["Body"].read()) == _envelopes()


@mock_aws
def test_s3_writer_refuses_empty_objects() -> None:
    s3 = boto3.client("s3", region_name="eu-west-2")
    with pytest.raises(ValueError, match="at least one"):
        S3RawWriter(s3, BUCKET).write(Feed.LINE_STATUS, POLLED_AT, POLL_ID, [])


def test_local_writer_uses_the_same_layout(tmp_path: Path) -> None:
    key = LocalRawWriter(tmp_path).write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())
    written = tmp_path / key
    assert written.is_file()
    assert decode_ndjson_gz(written.read_bytes()) == _envelopes()


@mock_aws
def test_s3_writer_never_overwrites_an_existing_object() -> None:
    s3 = boto3.client("s3", region_name="eu-west-2")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    writer = S3RawWriter(s3, BUCKET)
    writer.write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())
    with pytest.raises(ClientError) as raised:
        writer.write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())
    assert raised.value.response["Error"]["Code"] == "PreconditionFailed"


def test_writers_reject_envelopes_from_another_poll(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not belong"):
        LocalRawWriter(tmp_path).write(Feed.LINE_STATUS, POLLED_AT, POLL_ID, _envelopes())


def test_local_writer_never_overwrites(tmp_path: Path) -> None:
    writer = LocalRawWriter(tmp_path)
    writer.write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())
    with pytest.raises(FileExistsError):
        writer.write(Feed.ARRIVALS, POLLED_AT, POLL_ID, _envelopes())
