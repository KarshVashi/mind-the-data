"""Per-minute poller: line status, line disruption, station and line-wide arrivals,
and stop disruption every 5 minutes.

Lambda entry point: ``mind_the_data.ingest.poller.handler``.
Local run: ``uv run python -m mind_the_data.ingest.poller --local --out ./tmp``.
"""

import argparse
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from mind_the_data.common.clock import ensure_utc, utc_now
from mind_the_data.common.config import Line, Mode, Station, distinct_station_ids
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

STOP_DISRUPTION_EVERY_MINUTES = 5


def modes_param(lines: Sequence[Line]) -> str:
    """Comma-separated modes in a fixed order, so request paths are stable."""
    present = {line.mode for line in lines}
    return ",".join(m.value for m in Mode if m in present)


def plan_poll(
    lines: Sequence[Line], stations: Sequence[Station], slot: datetime
) -> list[PlannedRequest]:
    """Requests for one poll. ``slot`` is the scheduled minute, which sets the cadence."""
    modes = modes_param(lines)
    planned = [
        PlannedRequest(Feed.LINE_STATUS, f"/Line/Mode/{modes}/Status"),
        PlannedRequest(Feed.LINE_DISRUPTION, f"/Line/Mode/{modes}/Disruption"),
    ]
    planned += [
        PlannedRequest(Feed.ARRIVALS, f"/StopPoint/{naptan}/Arrivals")
        for naptan in distinct_station_ids(stations)
    ]
    if slot.minute % STOP_DISRUPTION_EVERY_MINUTES == 0:
        planned.append(PlannedRequest(Feed.STOP_DISRUPTION, f"/StopPoint/Mode/{modes}/Disruption"))
    # Line-wide boards (ADR-016) go last: requests take concurrency slots in
    # plan order, so a slow line endpoint can only use up its own share of
    # the budget, never the station boards' continuous history.
    planned += [PlannedRequest(Feed.LINE_ARRIVALS, f"/Line/{line.id}/Arrivals") for line in lines]
    return planned


def scheduled_slot(event: Mapping[str, Any], fallback: datetime) -> datetime:
    """The schedule's own time when present, so a late run keeps its minute's cadence."""
    raw = event.get("scheduled_time")
    if isinstance(raw, str):
        try:
            return ensure_utc(datetime.fromisoformat(raw))
        except ValueError:
            logger.warning("unparseable scheduled_time; using the clock")
    return fallback


def run(
    settings: runtime.Settings,
    writer: RawWriter,
    app_key: str | None,
    *,
    budget_s: float | None = None,
    slot: datetime | None = None,
    client_factory: Callable[..., TflClient] | None = None,
) -> CollectionSummary:
    lines, stations = runtime.config(settings.config_dir)
    polled_at = utc_now()
    return run_collection(
        plan_poll(lines, stations, slot or polled_at),
        writer=writer,
        app_key=app_key,
        polled_at=polled_at,
        budget_s=budget_s if budget_s is not None else settings.budget_s,
        max_concurrency=settings.max_concurrency,
        namespace=settings.metric_namespace,
        message="poll complete",
        client_factory=client_factory or TflClient,
        on_auth_failure=runtime.app_key.cache_clear,
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = runtime.Settings.from_env(os.environ)
    runtime.cold_start(settings.log_level)
    key, _ = runtime.app_key()
    summary = run(
        settings,
        runtime.raw_writer(settings),
        key,
        budget_s=runtime.budget_for(settings, context),
        slot=scheduled_slot(event, utc_now()),
    )
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
    runtime.cold_start(settings.log_level)
    key, source = resolve_app_key(env, _no_ssm)
    logger.info("app key resolved", extra={"key_source": source.value})
    try:
        run(settings, runtime.raw_writer(settings, local_out=args.out), key)
    except CollectionFailed as exc:
        logger.error("poll failed", extra={"reason": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
