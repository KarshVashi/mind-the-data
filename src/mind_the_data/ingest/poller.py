"""Per-minute poller: line status, line disruption, arrivals, and stop disruption every 5 minutes.

Lambda entry point: ``mind_the_data.ingest.poller.handler``.
Local run: ``uv run python -m mind_the_data.ingest.poller --local --out ./tmp``.
"""

import argparse
import asyncio
import logging
import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from mind_the_data.common.clock import utc_now
from mind_the_data.common.config import Line, Mode, Station, distinct_station_ids
from mind_the_data.common.raw import Feed
from mind_the_data.common.telemetry import configure_logging
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import runtime
from mind_the_data.ingest.app_key import resolve_app_key
from mind_the_data.ingest.client import TflClient
from mind_the_data.ingest.collect import CollectionSummary, PlannedRequest, collect, report

logger = logging.getLogger(__name__)

STOP_DISRUPTION_EVERY_MINUTES = 5


def modes_param(lines: Sequence[Line]) -> str:
    """Comma-separated modes in a fixed order, so request paths are stable."""
    present = {line.mode for line in lines}
    return ",".join(m.value for m in Mode if m in present)


def plan_poll(
    lines: Sequence[Line], stations: Sequence[Station], polled_at: datetime
) -> list[PlannedRequest]:
    modes = modes_param(lines)
    planned = [
        PlannedRequest(Feed.LINE_STATUS, f"/Line/Mode/{modes}/Status"),
        PlannedRequest(Feed.LINE_DISRUPTION, f"/Line/Mode/{modes}/Disruption"),
    ]
    planned += [
        PlannedRequest(Feed.ARRIVALS, f"/StopPoint/{naptan}/Arrivals")
        for naptan in distinct_station_ids(stations)
    ]
    if polled_at.minute % STOP_DISRUPTION_EVERY_MINUTES == 0:
        planned.append(PlannedRequest(Feed.STOP_DISRUPTION, f"/StopPoint/Mode/{modes}/Disruption"))
    return planned


async def poll_once(
    client: TflClient,
    writer: RawWriter,
    lines: Sequence[Line],
    stations: Sequence[Station],
    *,
    budget_s: float,
    polled_at: datetime | None = None,
) -> CollectionSummary:
    when = polled_at or utc_now()
    return await collect(
        client,
        writer,
        plan_poll(lines, stations, when),
        poll_id=uuid4(),
        polled_at=when,
        budget_s=budget_s,
    )


def run(
    settings: runtime.Settings,
    writer: RawWriter,
    app_key: str | None,
    client_factory: Callable[[str | None], TflClient] | None = None,
) -> CollectionSummary:
    lines, stations = runtime.config(settings.config_dir)

    async def go() -> CollectionSummary:
        # One client per invocation: its connection pool belongs to this event loop.
        async with (client_factory or TflClient)(app_key) as client:
            return await poll_once(client, writer, lines, stations, budget_s=settings.poll_budget_s)

    summary = asyncio.run(go())
    report(summary, settings.metric_namespace, "poll complete")
    if summary.write_failures:
        # Fail the invocation so the error alarm fires; the other feeds were written.
        failed = ", ".join(f.value for f in summary.write_failures)
        raise RuntimeError(f"raw write failed for: {failed}")
    return summary


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = runtime.Settings.from_env(os.environ)
    configure_logging(settings.log_level)
    key, source = runtime.app_key()
    logger.info("app key source", extra={"key_source": source.value})
    summary = run(settings, runtime.raw_writer(settings), key)
    return summary.log_fields()


def _no_ssm() -> NoReturn:
    raise RuntimeError("local runs read TFL_APP_KEY from the environment, never SSM")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one poll.")
    parser.add_argument("--local", action="store_true", help="write to a local directory")
    parser.add_argument("--out", type=Path, default=Path("tmp"), help="local output directory")
    args = parser.parse_args(argv)
    if not args.local:
        parser.error("only --local runs are supported outside Lambda")

    env = {k: v for k, v in os.environ.items() if k != "TFL_APP_KEY_PARAMETER"}
    settings = runtime.Settings.from_env(env)
    configure_logging(settings.log_level)
    key, source = resolve_app_key(env, _no_ssm)
    logger.info("app key source", extra={"key_source": source.value})
    summary = run(settings, runtime.raw_writer(settings, local_out=args.out), key)
    return 0 if summary.write_failures == [] else 1


if __name__ == "__main__":
    raise SystemExit(main())
