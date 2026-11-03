"""Raw layer contract (ADR-003): feed names, the envelope and the object key.

Every raw object holds one poll of one feed as gzipped NDJSON, one envelope
line per HTTP request. Failed requests are written too, so that missing data
can never be mistaken for a quiet period.
"""

import gzip
import json
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

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

    # Request path with its query parameters sorted and any app key removed.
    path: str


class Envelope(BaseModel):
    """One HTTP request and its unchanged TfL response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_id: UUID
    polled_at: datetime
    feed: Feed
    request: RequestInfo
    # None when no response arrived (timeout, connection error, budget spent).
    http_status: int | None = Field(ge=100, le=599)
    latency_ms: int | None = Field(default=None, ge=0)
    # Failure type for anything that is not a usable response, else None.
    error: str | None = None
    # Unchanged response body; None for failures with no usable body.
    payload: Any = None

    @field_validator("polled_at")
    @classmethod
    def _polled_at_is_utc(cls, value: datetime) -> datetime:
        # Fail when the envelope is built, not later when the object is written.
        return ensure_utc(value)

    @field_serializer("polled_at")
    def _serialise_polled_at(self, value: datetime) -> str:
        return isoformat_ms(value)

    @property
    def ok(self) -> bool:
        """A usable response: 2xx and nothing flagged (e.g. a non-JSON body is not ok)."""
        return self.http_status is not None and 200 <= self.http_status < 300 and self.error is None


def object_key(feed: Feed, polled_at: datetime, poll_id: UUID) -> str:
    """S3 key for one feed's object from one poll; partitions are UTC."""
    utc = ensure_utc(polled_at)
    stamp = utc.strftime("%Y%m%dT%H%M%SZ")
    return (
        f"raw/{feed.value}/dt={utc:%Y-%m-%d}/hour={utc:%H}/{feed.value}_{stamp}_{poll_id}.ndjson.gz"
    )


def check_one_poll(
    envelopes: Sequence[Envelope], feed: Feed, poll_id: UUID, polled_at: datetime
) -> None:
    """Every line of one object must belong to the same feed and poll (ADR-003)."""
    if not envelopes:
        raise ValueError("a raw object needs at least one envelope")
    expected = (feed, poll_id, ensure_utc(polled_at))
    for env in envelopes:
        if (env.feed, env.poll_id, env.polled_at) != expected:
            raise ValueError(
                f"envelope for {env.feed}/{env.poll_id} does not belong to {feed}/{poll_id}"
            )


def encode_ndjson_gz(envelopes: Sequence[Envelope]) -> bytes:
    lines = (
        json.dumps(e.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
        for e in envelopes
    )
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), mtime=0)


def decode_ndjson_gz(data: bytes) -> list[Envelope]:
    text = gzip.decompress(data).decode("utf-8")
    return [Envelope.model_validate_json(line) for line in text.splitlines() if line]
