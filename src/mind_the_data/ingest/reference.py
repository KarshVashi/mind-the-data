"""Daily reference fetch: route sequences, timetables and severity codes.

Lambda entry point: ``mind_the_data.ingest.reference.handler``.
Local run: ``uv run python -m mind_the_data.ingest.reference --local --out ./tmp``.

The timetable endpoint needs an explicit direction: without one TfL returns a
disambiguation instead of a timetable, so each station is fetched once per
direction.

Every run fetches severity codes and every line's route sequences. Timetables
are spread over the week (ADR-016), keyed on the run's UTC weekday:

- Monday: the measurement stations in ``config/stations.yaml``, as before M8.
- Tuesday to Sunday: one sixth each of every other station on lines whose mode
  has timetables, taken from the route sequences fetched earlier in the same
  run. A station keeps its day for as long as it stays on the line, so each
  one is refreshed weekly and a run never fetches the whole network at once.
"""

import argparse
import logging
import os
import zlib
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn

from mind_the_data.common.clock import ensure_utc, utc_now
from mind_the_data.common.config import Line, Mode, Station
from mind_the_data.common.raw import Feed
from mind_the_data.common.writer import RawWriter
from mind_the_data.ingest import models, runtime
from mind_the_data.ingest.app_key import resolve_app_key
from mind_the_data.ingest.client import RequestResult, TflClient
from mind_the_data.ingest.collect import (
    CollectionFailed,
    CollectionSummary,
    FollowUp,
    PlannedRequest,
    run_collection,
)

logger = logging.getLogger(__name__)

DIRECTIONS = ("inbound", "outbound")
# date.weekday(): Monday is 0. Measurement stations keep the Monday fetch they
# had when the job ran weekly; the rest of the network shares the other days.
MEASUREMENT_WEEKDAY = 0
NETWORK_WEEKDAYS = (1, 2, 3, 4, 5, 6)
# TfL answers 404 to every Elizabeth line and Overground timetable request
# (all 910G stations, every weekly run since 3 November), so the network
# fetch skips those modes. Their measurement stations are still fetched on
# Mondays, which shows when TfL starts publishing them.
NETWORK_TIMETABLE_MODES = frozenset({Mode.TUBE, Mode.DLR, Mode.TRAM})


def network_weekday(line_id: str, naptan_id: str) -> int:
    """The weekday a station's timetables are fetched: stable across runs and releases.

    CRC-32 rather than ``hash()``, which is salted per process.
    """
    key = f"{line_id}/{naptan_id}".encode()
    return NETWORK_WEEKDAYS[zlib.crc32(key) % len(NETWORK_WEEKDAYS)]


def run_date(event: Mapping[str, Any], fallback: datetime) -> date:
    """UTC date of the scheduled run, so a retried invocation fetches the same slice."""
    raw = event.get("scheduled_time")
    if isinstance(raw, str):
        try:
            return ensure_utc(datetime.fromisoformat(raw)).date()
        except ValueError:
            logger.warning("unparseable scheduled_time; using the clock")
    return ensure_utc(fallback).date()


def _timetable(line_id: str, naptan_id: str, direction: str) -> PlannedRequest:
    return PlannedRequest(
        Feed.REF_TIMETABLE, f"/Line/{line_id}/Timetable/{naptan_id}", {"direction": direction}
    )


def _route_sequence_path(line_id: str, direction: str) -> str:
    return f"/Line/{line_id}/Route/Sequence/{direction}"


def plan_reference(
    lines: Sequence[Line], stations: Sequence[Station], day: date
) -> list[PlannedRequest]:
    """The first round: severity, route sequences and, on Mondays, measurement stations."""
    planned = [PlannedRequest(Feed.REF_SEVERITY, "/Line/Meta/Severity")]
    planned += [
        PlannedRequest(Feed.REF_ROUTE_SEQUENCE, _route_sequence_path(line.id, direction))
        for line in lines
        for direction in DIRECTIONS
    ]
    if day.weekday() == MEASUREMENT_WEEKDAY:
        planned += [
            _timetable(station.line_id, station.naptan_id, direction)
            for station in stations
            for direction in DIRECTIONS
        ]
    return planned


def line_stations(
    lines: Sequence[Line], responses: Sequence[tuple[PlannedRequest, RequestResult]]
) -> dict[str, set[str]]:
    """Station ids on each line, from that line's successful route sequences.

    The line comes from the request, not the payload: the timetable request is
    made for the line that was asked about. A line whose route sequences both
    failed or did not parse has no stations this run, which shows as fewer
    timetable requests rather than a crash.
    """
    path_to_line = {
        _route_sequence_path(line.id, direction): line.id
        for line in lines
        for direction in DIRECTIONS
    }
    found: dict[str, set[str]] = {}
    for request, result in responses:
        line_id = path_to_line.get(request.path)
        if request.feed is not Feed.REF_ROUTE_SEQUENCE or line_id is None or not result.ok:
            continue
        try:
            sequence = models.parse_route_sequence(result.payload)
        except Exception:  # noqa: BLE001 - counted as invalid by collect; skip here
            logger.warning("route sequence did not parse", extra={"path": request.path})
            continue
        stops = found.setdefault(line_id, set())
        for route in sequence.ordered_line_routes:
            stops.update(route.naptan_ids)
    return found


def plan_network_timetables(
    lines: Sequence[Line],
    stations: Sequence[Station],
    day: date,
    stations_by_line: Mapping[str, set[str]],
) -> list[PlannedRequest]:
    """Today's share of every non-measurement station, both directions, in a stable order."""
    weekday = day.weekday()
    measured = {(s.line_id, s.naptan_id) for s in stations}
    planned: list[PlannedRequest] = []
    for line in lines:
        if line.mode not in NETWORK_TIMETABLE_MODES:
            continue
        for naptan_id in sorted(stations_by_line.get(line.id, ())):
            if (line.id, naptan_id) in measured:
                continue
            if network_weekday(line.id, naptan_id) != weekday:
                continue
            planned += [_timetable(line.id, naptan_id, d) for d in DIRECTIONS]
    return planned


def network_follow_up(lines: Sequence[Line], stations: Sequence[Station], day: date) -> FollowUp:
    def plan(responses: Sequence[tuple[PlannedRequest, RequestResult]]) -> list[PlannedRequest]:
        by_line = line_stations(lines, responses)
        planned = plan_network_timetables(lines, stations, day, by_line)
        logger.info(
            "network timetables planned",
            extra={
                "weekday": day.weekday(),
                "lines_with_stations": len(by_line),
                "timetable_requests": len(planned),
            },
        )
        return planned

    return plan


def run(
    settings: runtime.Settings,
    writer: RawWriter,
    app_key: str | None,
    *,
    budget_s: float | None = None,
    day: date | None = None,
    client_factory: Callable[..., TflClient] | None = None,
) -> CollectionSummary:
    lines, stations = runtime.config(settings.config_dir)
    polled_at = utc_now()
    day = day or polled_at.date()
    return run_collection(
        plan_reference(lines, stations, day),
        writer=writer,
        app_key=app_key,
        polled_at=polled_at,
        budget_s=budget_s if budget_s is not None else settings.budget_s,
        max_concurrency=settings.max_concurrency,
        namespace=settings.metric_namespace,
        message="reference fetch complete",
        client_factory=client_factory or TflClient,
        on_auth_failure=runtime.app_key.cache_clear,
        follow_up=network_follow_up(lines, stations, day),
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
        day=run_date(event, utc_now()),
    )
    return summary.log_fields()


def _no_ssm() -> NoReturn:
    raise RuntimeError("local runs read TFL_APP_KEY from the environment, never SSM")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one reference fetch.")
    parser.add_argument("--local", action="store_true", help="write to a local directory")
    parser.add_argument("--out", type=Path, default=Path("tmp"), help="local output directory")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="fetch this UTC date's timetable share (default: today)",
    )
    args = parser.parse_args(argv)
    if not args.local:
        parser.error("only --local runs are supported outside Lambda")

    env = {k: v for k, v in os.environ.items() if k != "TFL_APP_KEY_PARAMETER"}
    settings = runtime.Settings.from_env(env)
    runtime.cold_start(settings.log_level)
    key, source = resolve_app_key(env, _no_ssm)
    logger.info("app key resolved", extra={"key_source": source.value})
    try:
        run(settings, runtime.raw_writer(settings, local_out=args.out), key, day=args.date)
    except CollectionFailed as exc:
        logger.error("reference fetch failed", extra={"reason": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
