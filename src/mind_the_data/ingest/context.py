"""Station context collector (ADR-019): lifts, live busyness, cycle hire docks,
and the line status TfL announces ahead of time. TfL keeps no history of any
of these, so a day not collected is lost.

Lambda entry point: ``mind_the_data.ingest.context.handler``.
Local run: ``uv run python -m mind_the_data.ingest.context --local --out ./tmp``.

Runs every minute, separately from the poller, so these feeds can never use up
the league feeds' poll budget or concurrency. The scheduled minute sets what
each run fetches:

- lift disruption: every minute (one call);
- live busyness: a fifth of the Tube stations each minute, so each station is
  read every 5 minutes at about 54 calls a minute rather than 270 in one burst;
- BikePoint: every fifth minute, offset from the poller's stop disruption;
- line status ahead: every 6 hours, today to 14 days ahead, so one lost
  invocation never costs a day of announcements.
"""

import argparse
import logging
import os
import zlib
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from mind_the_data.common.clock import utc_now
from mind_the_data.common.config import CrowdingStation, Line
from mind_the_data.common.raw import Feed
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import runtime
from mind_the_data.ingest.app_key import resolve_app_key
from mind_the_data.ingest.client import TflClient
from mind_the_data.ingest.collect import (
    CollectionFailed,
    CollectionSummary,
    PlannedRequest,
    run_collection,
)

logger = logging.getLogger(__name__)

CROWDING_SLICES = 5
BIKE_POINT_EVERY_MINUTES = 5
# The poller reads stop disruption at minute % 5 == 0; BikePoint goes elsewhere.
BIKE_POINT_MINUTE = 2
AHEAD_EVERY_HOURS = 6
AHEAD_MINUTE = 7
AHEAD_DAYS = 14
# Only the lift feed's metrics feed an alarm (no objects written). Each feed's
# metrics are two billed custom metrics, so the others stay in the log line.
METRIC_FEEDS = frozenset({Feed.LIFT_DISRUPTION})
PARTIAL_FAILURE_SHARE = 0.2


def crowding_slice(naptan_id: str) -> int:
    """The minute (mod 5) a station is read: stable across runs and releases.

    CRC-32 rather than ``hash()``, which is salted per process.
    """
    return zlib.crc32(naptan_id.encode()) % CROWDING_SLICES


def ahead_path(lines: Sequence[Line], slot: datetime) -> str:
    """Every configured line, from the slot's UTC date to ``AHEAD_DAYS`` later.

    ``Line/Mode/{modes}/Status/{from}/to/{to}`` answers 404, so the lines are
    named. Whole dates: TfL returns every status whose validity overlaps them.
    """
    ids = ",".join(line.id for line in lines)
    start = slot.date()
    end = start + timedelta(days=AHEAD_DAYS)
    return f"/Line/{ids}/Status/{start:%Y-%m-%d}/to/{end:%Y-%m-%d}"


def plan_context(
    lines: Sequence[Line], stations: Sequence[CrowdingStation], slot: datetime
) -> list[PlannedRequest]:
    """Requests for one run. ``slot`` is the scheduled minute, UTC."""
    planned = [PlannedRequest(Feed.LIFT_DISRUPTION, "/Disruptions/Lifts/v2")]
    if slot.minute % BIKE_POINT_EVERY_MINUTES == BIKE_POINT_MINUTE:
        planned.append(PlannedRequest(Feed.BIKE_POINT, "/BikePoint"))
    if slot.hour % AHEAD_EVERY_HOURS == 0 and slot.minute == AHEAD_MINUTE:
        planned.append(PlannedRequest(Feed.LINE_STATUS_AHEAD, ahead_path(lines, slot)))
    current = slot.minute % CROWDING_SLICES
    planned += [
        PlannedRequest(Feed.CROWDING, f"/crowding/{s.naptan_id}/Live")
        for s in sorted(stations, key=lambda s: s.naptan_id)
        if crowding_slice(s.naptan_id) == current
    ]
    return planned


def run(
    settings: runtime.Settings,
    writer: RawWriter,
    app_key: str | None,
    *,
    budget_s: float | None = None,
    slot: datetime | None = None,
    client_factory: Callable[..., TflClient] | None = None,
) -> CollectionSummary:
    lines, _ = runtime.config(settings.config_dir)
    stations = runtime.crowding_stations(settings.config_dir)
    polled_at = utc_now()
    summary = run_collection(
        plan_context(lines, stations, slot or polled_at),
        writer=writer,
        app_key=app_key,
        polled_at=polled_at,
        budget_s=budget_s if budget_s is not None else settings.budget_s,
        max_concurrency=settings.max_concurrency,
        namespace=settings.metric_namespace,
        message="context poll complete",
        client_factory=client_factory or TflClient,
        on_auth_failure=runtime.app_key.cache_clear,
        metric_feeds=METRIC_FEEDS,
    )
    warn_partial_failures(summary)
    return summary


def warn_partial_failures(summary: CollectionSummary) -> list[Feed]:
    """Log a warning for each feed that lost a large share, but not all, of its requests.

    A whole-feed outage fails the run (and alarms); losing part of the
    busyness slice, say to 429s or stations that start answering 404, does
    not, so it is at least visible in the logs.
    """
    partial = sorted(
        feed
        for feed, outcome in summary.feeds.items()
        if outcome.requests
        and not outcome.all_failed
        and outcome.failures / outcome.requests >= PARTIAL_FAILURE_SHARE
    )
    for feed in partial:
        outcome = summary.feeds[feed]
        logger.warning(
            "feed partly failed",
            extra={"feed": feed.value, "requests": outcome.requests, "failures": outcome.failures},
        )
    return partial


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = runtime.Settings.from_env(os.environ)
    runtime.cold_start(settings.log_level)
    key, _ = runtime.app_key()
    summary = run(
        settings,
        runtime.raw_writer(settings),
        key,
        budget_s=runtime.budget_for(settings, context),
        slot=runtime.scheduled_slot(event, utc_now()),
    )
    return summary.log_fields()


def _no_ssm() -> NoReturn:
    raise RuntimeError("local runs read TFL_APP_KEY from the environment, never SSM")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one context poll.")
    parser.add_argument("--local", action="store_true", help="write to a local directory")
    parser.add_argument("--out", type=Path, default=Path("tmp"), help="local output directory")
    parser.add_argument(
        "--slot",
        type=datetime.fromisoformat,
        help="scheduled minute to plan for, ISO 8601 with a zone (default: now)",
    )
    args = parser.parse_args(argv)
    if not args.local:
        parser.error("only --local runs are supported outside Lambda")

    env = {k: v for k, v in os.environ.items() if k != "TFL_APP_KEY_PARAMETER"}
    settings = runtime.Settings.from_env(env)
    runtime.cold_start(settings.log_level)
    key, source = resolve_app_key(env, _no_ssm)
    logger.info("app key resolved", extra={"key_source": source.value})
    slot = (
        runtime.scheduled_slot({"scheduled_time": args.slot.isoformat()}, utc_now())
        if args.slot
        else None
    )
    try:
        run(settings, runtime.raw_writer(settings, local_out=args.out), key, slot=slot)
    except CollectionFailed as exc:
        logger.error("context poll failed", extra={"reason": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
