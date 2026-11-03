"""Shared collection step for the poller and reference Lambdas.

Plan the requests for each feed, run them inside one budget, then write one
raw object per feed. Raw is written before, and regardless of, validation
(ADR-003): validation only produces counts for the poll's log line.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from mind_the_data.common.raw import Envelope, Feed, RequestInfo
from mind_the_data.common.telemetry import emf_record, print_emf
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import models
from mind_the_data.ingest.client import RequestResult, TflClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannedRequest:
    feed: Feed
    path: str
    params: Mapping[str, str] | None = None


@dataclass
class FeedOutcome:
    requests: int = 0
    failures: int = 0
    records: int = 0
    invalid_records: int = 0
    written_key: str | None = None
    write_error: str | None = None


@dataclass
class CollectionSummary:
    poll_id: UUID
    polled_at: datetime
    duration_ms: int
    feeds: dict[Feed, FeedOutcome] = field(default_factory=dict)

    @property
    def write_failures(self) -> list[Feed]:
        return [f for f, o in self.feeds.items() if o.write_error is not None]

    def log_fields(self) -> dict[str, Any]:
        return {
            "poll_id": str(self.poll_id),
            "polled_at": self.polled_at.isoformat(),
            "duration_ms": self.duration_ms,
            "requests": sum(o.requests for o in self.feeds.values()),
            "request_failures": sum(o.failures for o in self.feeds.values()),
            "objects_written": sum(o.written_key is not None for o in self.feeds.values()),
            "invalid_records": sum(o.invalid_records for o in self.feeds.values()),
            "feeds": {
                f.value: {
                    "requests": o.requests,
                    "failures": o.failures,
                    "records": o.records,
                    "invalid_records": o.invalid_records,
                    "written": o.written_key is not None,
                    "write_error": o.write_error,
                }
                for f, o in sorted(self.feeds.items())
            },
        }


# Each feed's payload is either a list of records or one document. Records are
# validated one by one, so one malformed prediction does not hide the rest.
_LIST_MODELS: dict[Feed, type[BaseModel]] = {
    Feed.LINE_STATUS: models.LineStatus,
    Feed.LINE_DISRUPTION: models.Disruption,
    Feed.ARRIVALS: models.Arrival,
    Feed.STOP_DISRUPTION: models.StopDisruption,
    Feed.REF_SEVERITY: models.Severity,
}
_DOCUMENT_PARSERS: dict[Feed, Callable[[Any], object]] = {
    Feed.REF_ROUTE_SEQUENCE: models.parse_route_sequence,
    Feed.REF_TIMETABLE: models.parse_timetable,
}


def count_records(feed: Feed, payload: Any) -> tuple[int, int]:
    """(valid, invalid) records in one successful response; never raises."""
    if feed in _DOCUMENT_PARSERS:
        try:
            _DOCUMENT_PARSERS[feed](payload)
        except (ValidationError, ValueError, TypeError):
            return 0, 1
        return 1, 0
    model = _LIST_MODELS[feed]
    if not isinstance(payload, list):
        return 0, 1
    valid = invalid = 0
    for item in payload:
        try:
            model.model_validate(item)
        except ValidationError:
            invalid += 1
        else:
            valid += 1
    return valid, invalid


def to_envelope(feed: Feed, result: RequestResult, poll_id: UUID, polled_at: datetime) -> Envelope:
    return Envelope(
        poll_id=poll_id,
        polled_at=polled_at,
        feed=feed,
        request=RequestInfo(path=result.path),
        http_status=result.http_status,
        latency_ms=result.latency_ms,
        error=result.error,
        payload=result.payload,
    )


async def collect(
    client: TflClient,
    writer: RawWriter,
    planned: Sequence[PlannedRequest],
    *,
    poll_id: UUID,
    polled_at: datetime,
    budget_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> CollectionSummary:
    started = clock()
    results = await client.get_many(
        [(p.path, p.params) for p in planned], deadline=started + budget_s
    )

    by_feed: dict[Feed, list[Envelope]] = {}
    summary = CollectionSummary(poll_id=poll_id, polled_at=polled_at, duration_ms=0)
    for request, result in zip(planned, results, strict=True):
        outcome = summary.feeds.setdefault(request.feed, FeedOutcome())
        outcome.requests += 1
        envelope = to_envelope(request.feed, result, poll_id, polled_at)
        by_feed.setdefault(request.feed, []).append(envelope)
        if envelope.ok:
            valid, invalid = count_records(request.feed, envelope.payload)
            outcome.records += valid
            outcome.invalid_records += invalid
        else:
            outcome.failures += 1

    # Every feed is written, failures included; one failed write does not
    # stop the others.
    for feed, envelopes in by_feed.items():
        outcome = summary.feeds[feed]
        try:
            outcome.written_key = writer.write(feed, polled_at, poll_id, envelopes)
        except Exception as exc:
            outcome.write_error = type(exc).__name__
            logger.exception("raw write failed", extra={"feed": feed.value})

    summary.duration_ms = round((clock() - started) * 1000)
    return summary


def report(summary: CollectionSummary, namespace: str, message: str) -> None:
    """One summary log line, plus one embedded-metric line per feed."""
    logger.info(message, extra=summary.log_fields())
    print_emf(
        emf_record(
            namespace,
            {
                feed.value: {
                    "ObjectsWritten": int(outcome.written_key is not None),
                    "RequestFailures": outcome.failures,
                }
                for feed, outcome in summary.feeds.items()
            },
            {"poll_id": str(summary.poll_id)},
            timestamp=summary.polled_at,
        )
    )
