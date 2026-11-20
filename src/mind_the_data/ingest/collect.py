"""Shared collection step for the poller and reference Lambdas.

Plan the requests for each feed, run them inside one budget, write one raw
object per feed, and only then validate (ADR-003): validation produces counts
for the log line and can never stop or delay a raw write.

A run fails (so the Lambda error alarm fires) when a raw write fails or when
every request for a feed failed, which is what a revoked key or a TfL outage
looks like. The raw objects for that run are still written first.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from mind_the_data.common.raw import Envelope, Feed, RequestInfo
from mind_the_data.common.telemetry import emf_record, print_emf
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import models
from mind_the_data.ingest.client import RequestResult, TflClient

logger = logging.getLogger(__name__)

AUTH_FAILURE_STATUSES = frozenset({401, 403})


class CollectionFailed(RuntimeError):
    """Raised after all writes, so the invocation fails and the alarm fires."""


@dataclass(frozen=True)
class PlannedRequest:
    feed: Feed
    path: str
    params: Mapping[str, str] | None = None


@dataclass
class FeedOutcome:
    requests: int = 0
    failures: int = 0
    auth_failures: int = 0
    records: int = 0
    invalid_records: int = 0
    written_key: str | None = None
    write_error: str | None = None

    @property
    def all_failed(self) -> bool:
        return self.requests > 0 and self.failures == self.requests


@dataclass
class CollectionSummary:
    poll_id: UUID
    polled_at: datetime
    duration_ms: int = 0
    feeds: dict[Feed, FeedOutcome] = field(default_factory=dict)
    # Set when planning the follow-up round raised; the first round is still written.
    follow_up_error: str | None = None

    @property
    def write_failures(self) -> list[Feed]:
        return sorted(f for f, o in self.feeds.items() if o.write_error is not None)

    @property
    def outage_feeds(self) -> list[Feed]:
        return sorted(f for f, o in self.feeds.items() if o.all_failed)

    @property
    def auth_failed(self) -> bool:
        return any(o.auth_failures for o in self.feeds.values())

    def log_fields(self) -> dict[str, Any]:
        return {
            "poll_id": str(self.poll_id),
            "polled_at": self.polled_at.isoformat(),
            "duration_ms": self.duration_ms,
            "requests": sum(o.requests for o in self.feeds.values()),
            "request_failures": sum(o.failures for o in self.feeds.values()),
            "objects_written": sum(o.written_key is not None for o in self.feeds.values()),
            "invalid_records": sum(o.invalid_records for o in self.feeds.values()),
            "follow_up_error": self.follow_up_error,
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
    Feed.LINE_ARRIVALS: models.Arrival,
    Feed.STOP_DISRUPTION: models.StopDisruption,
    Feed.REF_SEVERITY: models.Severity,
    Feed.LIFT_DISRUPTION: models.LiftDisruption,
    Feed.BIKE_POINT: models.BikePoint,
    # Same Line shape as the live status, for a window of dates (ADR-019).
    Feed.LINE_STATUS_AHEAD: models.LineStatus,
}


def _timetable_is_usable(payload: Any) -> bool:
    # A disambiguation means TfL wanted a direction: no timetable was returned.
    return models.parse_timetable(payload).timetable is not None


_DOCUMENT_CHECKS: dict[Feed, Callable[[Any], object]] = {
    Feed.REF_ROUTE_SEQUENCE: models.parse_route_sequence,
    Feed.REF_TIMETABLE: _timetable_is_usable,
    # A station without live data still parses: it is a valid "no data" record.
    Feed.CROWDING: models.parse_crowding_live,
}


def count_records(feed: Feed, payload: Any) -> tuple[int, int]:
    """(valid, invalid) records in one successful response. Never raises."""
    if feed in _DOCUMENT_CHECKS:
        try:
            usable = _DOCUMENT_CHECKS[feed](payload)
        except Exception:  # noqa: BLE001 - any parse problem is an invalid record
            return 0, 1
        return (0, 1) if usable is False else (1, 0)
    if not isinstance(payload, list):
        return 0, 1
    model = _LIST_MODELS[feed]
    valid = invalid = 0
    for item in payload:
        try:
            model.model_validate(item)
        except Exception:  # noqa: BLE001 - any parse problem is an invalid record
            invalid += 1
        else:
            valid += 1
    return valid, invalid


# Plans more requests from the first round's responses (the reference fetch
# plans timetables from the route sequences it has just fetched).
FollowUp = Callable[[Sequence[tuple[PlannedRequest, RequestResult]]], Sequence[PlannedRequest]]


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
    follow_up: FollowUp | None = None,
) -> CollectionSummary:
    started = clock()
    deadline = started + budget_s
    planned = list(planned)
    results = await client.get_many([(p.path, p.params) for p in planned], deadline=deadline)
    summary = CollectionSummary(poll_id=poll_id, polled_at=polled_at)
    if follow_up is not None:
        more: list[PlannedRequest] = []
        try:
            more = list(follow_up(list(zip(planned, results, strict=True))))
        except Exception as exc:
            # A planning bug must not cost the responses already in hand; the
            # run still fails after the writes, so the alarm fires.
            summary.follow_up_error = type(exc).__name__
            logger.exception("follow-up planning failed")
        # Shares the deadline: the follow-up gets whatever budget is left.
        results += await client.get_many([(p.path, p.params) for p in more], deadline=deadline)
        planned += more

    by_feed: dict[Feed, list[Envelope]] = {}
    for request, result in zip(planned, results, strict=True):
        by_feed.setdefault(request.feed, []).append(
            to_envelope(request.feed, result, poll_id, polled_at)
        )

    # 1. Write every feed, failures included; one failed write never stops the others.
    for feed, envelopes in by_feed.items():
        outcome = summary.feeds.setdefault(feed, FeedOutcome())
        try:
            outcome.written_key = writer.write(feed, polled_at, poll_id, envelopes)
        except Exception as exc:
            outcome.write_error = type(exc).__name__
            logger.exception("raw write failed", extra={"feed": feed.value})

    # 2. Only then count and validate.
    for feed, envelopes in by_feed.items():
        outcome = summary.feeds[feed]
        for env in envelopes:
            outcome.requests += 1
            if not env.ok:
                outcome.failures += 1
                if env.http_status in AUTH_FAILURE_STATUSES:
                    outcome.auth_failures += 1
                continue
            valid, invalid = count_records(feed, env.payload)
            outcome.records += valid
            outcome.invalid_records += invalid

    summary.duration_ms = round((clock() - started) * 1000)
    return summary


def report(
    summary: CollectionSummary,
    namespace: str,
    message: str,
    metric_feeds: frozenset[Feed] | None = None,
) -> None:
    """One summary log line, plus one embedded-metric line per feed.

    ``metric_feeds`` limits the metric lines to those feeds (None: every feed).
    Each feed's metrics are billed custom metrics, so a collector whose other
    feeds no alarm reads keeps them to the log line.
    """
    logger.info(message, extra=summary.log_fields())
    feeds = {
        feed: outcome
        for feed, outcome in summary.feeds.items()
        if metric_feeds is None or feed in metric_feeds
    }
    if not feeds:
        return
    print_emf(
        emf_record(
            namespace,
            {
                feed.value: {
                    "ObjectsWritten": int(outcome.written_key is not None),
                    "RequestFailures": outcome.failures,
                }
                for feed, outcome in feeds.items()
            },
            {"poll_id": str(summary.poll_id)},
            timestamp=summary.polled_at,
        )
    )


def run_collection(
    planned: Sequence[PlannedRequest],
    *,
    writer: RawWriter,
    app_key: str | None,
    polled_at: datetime,
    budget_s: float,
    max_concurrency: int,
    namespace: str,
    message: str,
    client_factory: Callable[..., TflClient] = TflClient,
    on_auth_failure: Callable[[], None] = lambda: None,
    follow_up: FollowUp | None = None,
    metric_feeds: frozenset[Feed] | None = None,
) -> CollectionSummary:
    """Run one collection end to end and raise if it should alarm."""

    async def go() -> CollectionSummary:
        # One client per invocation: its connection pool belongs to this event loop.
        async with client_factory(app_key, max_concurrency=max_concurrency) as client:
            return await collect(
                client,
                writer,
                planned,
                poll_id=uuid4(),
                polled_at=polled_at,
                budget_s=budget_s,
                follow_up=follow_up,
            )

    summary = asyncio.run(go())
    report(summary, namespace, message, metric_feeds)

    if summary.auth_failed:
        # The cached key may have been rotated or revoked: fetch it afresh next time.
        on_auth_failure()
    problems = [f"raw write failed for {f.value}" for f in summary.write_failures]
    problems += [f"every request failed for {f.value}" for f in summary.outage_feeds]
    if summary.follow_up_error is not None:
        problems.append(f"follow-up planning failed: {summary.follow_up_error}")
    if problems:
        raise CollectionFailed("; ".join(problems))
    return summary
