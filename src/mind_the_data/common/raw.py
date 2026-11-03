"""Raw layer contract (ADR-003): feed names, the envelope and the object key.

Every raw object holds one poll of one feed as gzipped NDJSON, one envelope
line per HTTP request. Failed requests are written too, so that missing data
can never be mistaken for a quiet period.
"""

import gzip
import json
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from mind_the_data.common.clock import ensure_utc, isoformat_ms


class Feed(StrEnum):
    LINE_STATUS = "line_status"
    LINE_DISRUPTION = "line_disruption"
    ARRIVALS = "arrivals"
    STOP_DISRUPTION = "stop_disruption"
    REF_ROUTE_SEQUENCE = "ref_route_sequence"
    REF_TIMETABLE = "ref_timetable"
    REF_SEVERITY = "ref_severity"


class RequestInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Path and query exactly as sent, minus any app_key.
    path: str


class Envelope(BaseModel):
    """One HTTP request and its unchanged TfL response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_id: UUID
    polled_at: datetime
    feed: Feed
    request: RequestInfo
    # None when no response arrived (timeout, connection error, budget spent).
    http_status: int | None
    latency_ms: int | None = Field(default=None, ge=0)
    # Failure type for requests without a usable response, else None.
    error: str | None = None
    payload: Any = None

    @field_serializer("polled_at")
    def _serialise_polled_at(self, value: datetime) -> str:
        return isoformat_ms(value)

    @property
    def ok(self) -> bool:
        return self.http_status is not None and 200 <= self.http_status < 300


def object_key(feed: Feed, polled_at: datetime, poll_id: UUID) -> str:
    """S3 key for one feed's object from one poll; partitions are UTC."""
    utc = ensure_utc(polled_at)
    stamp = utc.strftime("%Y%m%dT%H%M%SZ")
    return (
        f"raw/{feed.value}/dt={utc:%Y-%m-%d}/hour={utc:%H}/{feed.value}_{stamp}_{poll_id}.ndjson.gz"
    )


def encode_ndjson_gz(envelopes: Iterable[Envelope]) -> bytes:
    lines = (
        json.dumps(e.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
        for e in envelopes
    )
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), mtime=0)


def decode_ndjson_gz(data: bytes) -> list[Envelope]:
    text = gzip.decompress(data).decode("utf-8")
    return [Envelope.model_validate_json(line) for line in text.splitlines() if line]
