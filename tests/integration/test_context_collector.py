"""Context collector end to end (ADR-019): recorded fixtures over a fake
transport, the real config, and a local raw writer. No network, no AWS."""

import json
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache, partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from mind_the_data.common.raw import Feed, decode_ndjson_gz
from mind_the_data.common.writer import LocalRawWriter
from mind_the_data.ingest import context, runtime
from mind_the_data.ingest.client import RetryPolicy, TflClient
from mind_the_data.ingest.collect import CollectionFailed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tfl"
KEY = "0f" * 16
SETTINGS = runtime.Settings.from_env({"METRIC_NAMESPACE": "MindTheData"})
# 00:07 UTC on a minute % 5 == 2: every feed is due.
SLOT_ALL = datetime(2026, 11, 20, 0, 7, tzinfo=UTC)
SLOT_LIFTS_AND_CROWDING = datetime(2026, 11, 20, 8, 16, tzinfo=UTC)
UNAVAILABLE_STATION = "940GZZLUWLO"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())["payload"]


def tfl_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/Disruptions/Lifts/v2":
        return httpx.Response(200, json=_fixture("lift_disruption"))
    if path == "/BikePoint":
        return httpx.Response(200, json=_fixture("bike_point"))
    if path.startswith("/Line/") and "/Status/" in path:
        return httpx.Response(200, json=_fixture("line_status_ahead"))
    if path == f"/crowding/{UNAVAILABLE_STATION}/Live":
        unavailable = {"dataAvailable": False, "percentageOfBaseline": 0, "timeUtc": None}
        return httpx.Response(200, json=unavailable)
    if path.startswith("/crowding/") and path.endswith("/Live"):
        return httpx.Response(200, json=_fixture("crowding_live_940GZZLUKSX"))
    return httpx.Response(404)


def factory(
    handler: Callable[[httpx.Request], httpx.Response] = tfl_handler,
) -> Callable[..., TflClient]:
    return partial(
        TflClient, transport=httpx.MockTransport(handler), retry=RetryPolicy(max_attempts=1)
    )


def written(root: Path) -> dict[str, list[Any]]:
    return {
        str(p.relative_to(root)): decode_ndjson_gz(p.read_bytes())
        for p in sorted(root.rglob("*.ndjson.gz"))
    }


def test_a_run_with_every_feed_due_writes_one_object_per_feed(tmp_path: Path) -> None:
    summary = context.run(
        SETTINGS, LocalRawWriter(tmp_path), KEY, slot=SLOT_ALL, client_factory=factory()
    )
    objects = written(tmp_path)
    assert {key.split("/")[1] for key in objects} == {
        "lift_disruption",
        "bike_point",
        "line_status_ahead",
        "crowding",
    }
    assert all(len(envs) >= 1 for envs in objects.values())
    crowding = summary.feeds[Feed.CROWDING]
    stations = runtime.crowding_stations(SETTINGS.config_dir)
    in_slice = [s for s in stations if context.crowding_slice(s.naptan_id) == SLOT_ALL.minute % 5]
    assert crowding.requests == len(in_slice)
    assert crowding.failures == 0
    assert crowding.invalid_records == 0
    assert summary.feeds[Feed.BIKE_POINT].records == len(_fixture("bike_point"))


def test_a_plain_minute_writes_lifts_and_one_slice_of_stations(tmp_path: Path) -> None:
    context.run(
        SETTINGS,
        LocalRawWriter(tmp_path),
        KEY,
        slot=SLOT_LIFTS_AND_CROWDING,
        client_factory=factory(),
    )
    assert {key.split("/")[1] for key in written(tmp_path)} == {"lift_disruption", "crowding"}


def test_the_key_never_reaches_raw(tmp_path: Path) -> None:
    context.run(SETTINGS, LocalRawWriter(tmp_path), KEY, slot=SLOT_ALL, client_factory=factory())
    for envelopes in written(tmp_path).values():
        for env in envelopes:
            assert KEY not in env.request.path
            assert "app_key" not in env.model_dump_json()


def test_a_lift_outage_is_written_then_fails_the_run(tmp_path: Path) -> None:
    def lifts_down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Disruptions/Lifts/v2":
            return httpx.Response(503)
        return tfl_handler(request)

    with pytest.raises(CollectionFailed, match="lift_disruption"):
        context.run(
            SETTINGS,
            LocalRawWriter(tmp_path),
            KEY,
            slot=SLOT_LIFTS_AND_CROWDING,
            client_factory=factory(lifts_down),
        )
    objects = written(tmp_path)
    lifts = next(envs for key, envs in objects.items() if "/lift_disruption/" in key)
    # The failure is on record, so an outage never reads as "no lifts down".
    assert lifts[0].http_status == 503
    assert lifts[0].payload is None
    assert any("/crowding/" in key for key in objects)


def test_handler_takes_its_minute_from_the_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = context.run
    seen: dict[str, Any] = {}

    def run_with_fake(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return original(*args, **kwargs, client_factory=factory())

    monkeypatch.setattr(context, "run", run_with_fake)
    monkeypatch.setattr(runtime, "app_key", cache(lambda: (KEY, None)))
    monkeypatch.setattr(runtime, "raw_writer", lambda settings: LocalRawWriter(tmp_path))
    result = context.handler({"scheduled_time": "2026-11-20T00:07:00Z"}, object())
    assert seen["slot"] == SLOT_ALL
    assert result["feeds"]["line_status_ahead"]["written"] is True


def test_local_cli_writes_partitioned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    original = context.run

    def run_with_fake(*args: Any, **kwargs: Any) -> Any:
        return original(*args, **kwargs, client_factory=factory())

    monkeypatch.setattr(context, "run", run_with_fake)
    argv = ["--local", "--out", str(tmp_path), "--slot", "2026-11-20T00:07:00+00:00"]
    assert context.main(argv) == 0
    keys = list(written(tmp_path))
    assert any(k.startswith("raw/line_status_ahead/dt=") for k in keys)


def test_one_station_failing_is_written_and_the_run_succeeds(tmp_path: Path) -> None:
    def one_down(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/crowding/{UNAVAILABLE_STATION}/Live":
            return httpx.Response(404)
        return tfl_handler(request)

    # 940GZZLUWLO is in minute 16's slice.
    assert context.crowding_slice(UNAVAILABLE_STATION) == SLOT_LIFTS_AND_CROWDING.minute % 5
    summary = context.run(
        SETTINGS,
        LocalRawWriter(tmp_path),
        KEY,
        slot=SLOT_LIFTS_AND_CROWDING,
        client_factory=factory(one_down),
    )
    assert summary.feeds[Feed.CROWDING].failures == 1
    crowding = next(envs for key, envs in written(tmp_path).items() if "/crowding/" in key)
    failed = [e for e in crowding if e.request.path == f"/crowding/{UNAVAILABLE_STATION}/Live"]
    assert [(e.http_status, e.payload) for e in failed] == [(404, None)]
    assert context.warn_partial_failures(summary) == []


def test_every_station_failing_is_written_then_fails_the_run(tmp_path: Path) -> None:
    def crowding_down(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/crowding/"):
            return httpx.Response(429)
        return tfl_handler(request)

    with pytest.raises(CollectionFailed, match="crowding"):
        context.run(
            SETTINGS,
            LocalRawWriter(tmp_path),
            KEY,
            slot=SLOT_LIFTS_AND_CROWDING,
            client_factory=factory(crowding_down),
        )
    crowding = next(envs for key, envs in written(tmp_path).items() if "/crowding/" in key)
    assert {e.http_status for e in crowding} == {429}


def test_a_large_partial_loss_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def half_down(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/crowding/") and zlib.crc32(path.encode()) % 2:
            return httpx.Response(429)
        return tfl_handler(request)

    with caplog.at_level("WARNING"):
        summary = context.run(
            SETTINGS,
            LocalRawWriter(tmp_path),
            KEY,
            slot=SLOT_LIFTS_AND_CROWDING,
            client_factory=factory(half_down),
        )
    assert context.warn_partial_failures(summary) == [Feed.CROWDING]
    assert any(r.getMessage() == "feed partly failed" for r in caplog.records)


def test_only_the_lift_feed_emits_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context.run(SETTINGS, LocalRawWriter(tmp_path), KEY, slot=SLOT_ALL, client_factory=factory())
    docs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"_aws"' in line]
    assert [d["Feed"] for d in docs] == ["lift_disruption"]
