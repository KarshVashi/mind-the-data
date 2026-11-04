"""Weekly reference fetch: route sequences, timetables and severity codes.

Lambda entry point: ``mind_the_data.ingest.reference.handler``.
Local run: ``uv run python -m mind_the_data.ingest.reference --local --out ./tmp``.

The timetable endpoint needs an explicit direction: without one TfL returns a
disambiguation instead of a timetable, so each station is fetched once per
direction.
"""

import argparse
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from mind_the_data.common.clock import utc_now
from mind_the_data.common.config import Line, Station
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


def run(
    settings: runtime.Settings,
    writer: RawWriter,
    app_key: str | None,
    *,
    budget_s: float | None = None,
    client_factory: Callable[..., TflClient] | None = None,
) -> CollectionSummary:
    lines, stations = runtime.config(settings.config_dir)
    return run_collection(
        plan_reference(lines, stations),
        writer=writer,
        app_key=app_key,
        polled_at=utc_now(),
        budget_s=budget_s if budget_s is not None else settings.budget_s,
        max_concurrency=settings.max_concurrency,
        namespace=settings.metric_namespace,
        message="reference fetch complete",
        client_factory=client_factory or TflClient,
        on_auth_failure=runtime.app_key.cache_clear,
    )


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = runtime.Settings.from_env(os.environ)
    runtime.cold_start(settings.log_level)
    key, _ = runtime.app_key()
    summary = run(
        settings, runtime.raw_writer(settings), key, budget_s=runtime.budget_for(settings, context)
    )
    return summary.log_fields()


def _no_ssm() -> NoReturn:
    raise RuntimeError("local runs read TFL_APP_KEY from the environment, never SSM")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one reference fetch.")
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
        logger.error("reference fetch failed", extra={"reason": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
