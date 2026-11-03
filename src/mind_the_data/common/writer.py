"""Write raw envelopes: to S3 in Lambda, or to a local directory for --local runs."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from mind_the_data.common.raw import Envelope, Feed, encode_ndjson_gz, object_key


class RawWriter(Protocol):
    def write(
        self, feed: Feed, polled_at: datetime, poll_id: UUID, envelopes: Sequence[Envelope]
    ) -> str: ...


class _S3Client(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object: ...


class S3RawWriter:
    def __init__(self, s3: _S3Client, bucket: str) -> None:
        self._s3 = s3
        self._bucket = bucket

    def write(
        self, feed: Feed, polled_at: datetime, poll_id: UUID, envelopes: Sequence[Envelope]
    ) -> str:
        if not envelopes:
            raise ValueError("refusing to write an empty raw object")
        key = object_key(feed, polled_at, poll_id)
        # No Content-Encoding: the .gz object is stored and read as-is by Athena.
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=encode_ndjson_gz(envelopes),
            ContentType="application/x-ndjson",
        )
        return key


class LocalRawWriter:
    """Same layout as S3 under a local directory, for one-off local polls."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self, feed: Feed, polled_at: datetime, poll_id: UUID, envelopes: Sequence[Envelope]
    ) -> str:
        if not envelopes:
            raise ValueError("refusing to write an empty raw object")
        key = object_key(feed, polled_at, poll_id)
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_ndjson_gz(envelopes))
        return key
