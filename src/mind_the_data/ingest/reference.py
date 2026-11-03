"""Weekly reference fetch: route sequences, timetables and severity codes.

Lambda entry point: ``mind_the_data.ingest.reference.handler``.

The timetable endpoint needs an explicit direction: without one TfL returns a
disambiguation instead of a timetable, so each station is fetched once per
direction.
"""

import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from mind_the_data.common.clock import utc_now
from mind_the_data.common.config import Line, Station
from mind_the_data.common.raw import Feed
from mind_the_data.common.telemetry import configure_logging
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import runtime
from mind_the_data.ingest.client import TflClient
from mind_the_data.ingest.collect import CollectionSummary, PlannedRequest, collect, report

logger = logging.getLogger(__name__)

DIRECTIONS = ("inbound", "outbound")


def plan_reference(lines: Sequence[Line], stations: Sequence[Station]) -> list[PlannedRequest]:
    planned = [PlannedRequest(Feed.REF_SEVERITY, "/Line/Meta/Severity")]
    planned += [
        PlannedRequest(Feed.REF_ROUTE_SEQUENCE, f"/Line/{line.id}/Route/Sequence/{direction}")
        for line in lines
        for direction in DIRECTIONS
    ]
    planned += [
        PlannedRequest(
            Feed.REF_TIMETABLE,
            f"/Line/{station.line_id}/Timetable/{station.naptan_id}",
            {"direction": direction},
        )
        for station in stations
        for direction in DIRECTIONS
    ]
    return planned


async def fetch_once(
    client: TflClient,
    writer: RawWriter,
    lines: Sequence[Line],
    stations: Sequence[Station],
    *,
    budget_s: float,
    fetched_at: datetime | None = None,
) -> CollectionSummary:
    return await collect(
        client,
        writer,
        plan_reference(lines, stations),
        poll_id=uuid4(),
        polled_at=fetched_at or utc_now(),
        budget_s=budget_s,
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = runtime.Settings.from_env(os.environ)
    configure_logging(settings.log_level)
    key, source = runtime.app_key()
    logger.info("app key source", extra={"key_source": source.value})
    lines, stations = runtime.config(settings.config_dir)
    writer = runtime.raw_writer(settings)

    async def go() -> CollectionSummary:
        async with TflClient(key) as client:
            return await fetch_once(
                client, writer, lines, stations, budget_s=settings.poll_budget_s
            )

    summary = asyncio.run(go())
    report(summary, settings.metric_namespace, "reference fetch complete")
    if summary.write_failures:
        failed = ", ".join(f.value for f in summary.write_failures)
        raise RuntimeError(f"raw write failed for: {failed}")
    return summary.log_fields()
