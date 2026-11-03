"""Poller and reference handlers end to end: recorded TfL fixtures over a fake
transport, real config, and moto S3 and SSM. No network."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws

from mind_the_data.common.config import distinct_station_ids, load_config
from mind_the_data.common.raw import Feed, decode_ndjson_gz
from mind_the_data.common.writer import LocalRawWriter, S3RawWriter
from mind_the_data.ingest import poller, reference, runtime
from mind_the_data.ingest.client import TflClient
from mind_the_data.ingest.collect import CollectionSummary, count_records

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tfl"
BUCKET = "mind-the-data-raw-123456789012"
KEY = "0f" * 16
FAILING_STATION = "940GZZLUBNK"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())["payload"]


def tfl_handler(request: httpx.Request) -> httpx.Response:
    """Serve recorded payloads; one station fails so failure envelopes are exercised."""
    path = request.url.path
    if path.endswith("/Status"):
        return httpx.Response(200, json=_fixture("line_status.json"))
    if path.startswith("/Line/Mode/") and path.endswith("/Disruption"):
        return httpx.Response(200, json=_fixture("line_disruption.json"))
    if path.startswith("/StopPoint/Mode/"):
        return httpx.Response(200, json=_fixture("stop_disruption.json"))
    if path == f"/StopPoint/{FAILING_STATION}/Arrivals":
        return httpx.Response(503)
    if path.endswith("/Arrivals"):
        naptan = path.split("/")[2]
        file = FIXTURES / f"arrivals_{naptan}.json"
        return httpx.Response(200, json=_fixture(file.name) if file.exists() else [])
    if path == "/Line/Meta/Severity":
        return httpx.Response(200, json=_fixture("severity.json"))
    if "/Route/Sequence/" in path:
        return httpx.Response(200, json=_fixture("route_sequence_victoria_inbound.json"))
    if "/Timetable/" in path:
        direction = request.url.params.get("direction", "inbound")
        return httpx.Response(
            200, json=_fixture(f"timetable_victoria_940GZZLUOXC_{direction}.json")
        )
    return httpx.Response(404)


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-west-2")
        s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
        )
        yield


def _objects(prefix: str = "raw/") -> dict[str, list[Any]]:
    s3 = boto3.client("s3", region_name="eu-west-2")
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])
    return {
        o["Key"]: decode_ndjson_gz(s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
        for o in listing
    }


def _poll(at: datetime, writer: Any) -> CollectionSummary:
    lines, stations = load_config()

    async def go() -> CollectionSummary:
        transport = httpx.MockTransport(tfl_handler)
        async with TflClient(KEY, transport=transport) as client:
            return await poller.poll_once(
                client, writer, lines, stations, budget_s=45, polled_at=at
            )

    return asyncio.run(go())


def test_poll_writes_one_object_per_feed_with_every_request(aws: None) -> None:
    at = datetime(2026, 11, 8, 8, 16, 0, tzinfo=UTC)  # not a 5-minute mark
    summary = _poll(at, S3RawWriter(boto3.client("s3", region_name="eu-west-2"), BUCKET))

    objects = _objects()
    feeds = {key.split("/")[1] for key in objects}
    assert feeds == {"line_status", "line_disruption", "arrivals"}
    assert all("/dt=2026-11-08/hour=08/" in key for key in objects)

    _, stations = load_config()
    (arrivals,) = (v for k, v in objects.items() if k.startswith("raw/arrivals/"))
    assert len(arrivals) == len(distinct_station_ids(stations)) == 50
    assert len({e.poll_id for env in objects.values() for e in env}) == 1

    failed = [e for e in arrivals if not e.ok]
    assert [e.request.path for e in failed] == [f"/StopPoint/{FAILING_STATION}/Arrivals"]
    assert failed[0].http_status == 503 and failed[0].payload is None

    assert summary.feeds[Feed.ARRIVALS].failures == 1
    assert summary.feeds[Feed.LINE_STATUS].records == 20
    assert summary.write_failures == []


def test_stop_disruption_only_on_five_minute_marks(aws: None) -> None:
    writer = S3RawWriter(boto3.client("s3", region_name="eu-west-2"), BUCKET)
    _poll(datetime(2026, 11, 8, 8, 20, tzinfo=UTC), writer)
    assert any(k.startswith("raw/stop_disruption/") for k in _objects())


def test_key_never_written_to_raw(aws: None) -> None:
    _poll(
        datetime(2026, 11, 8, 8, 20, tzinfo=UTC),
        S3RawWriter(boto3.client("s3", region_name="eu-west-2"), BUCKET),
    )
    for envelopes in _objects().values():
        for env in envelopes:
            assert KEY not in env.model_dump_json()


def test_write_failure_still_writes_other_feeds_and_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FlakyWriter(LocalRawWriter):
        def write(self, feed: Feed, *args: Any, **kwargs: Any) -> str:
            if feed is Feed.ARRIVALS:
                raise OSError("disk full")
            return super().write(feed, *args, **kwargs)

    monkeypatch.setenv("CONFIG_DIR", str(runtime.Settings.from_env({}).config_dir))
    settings = runtime.Settings.from_env({"POLL_BUDGET_SECONDS": "45"})
    with pytest.raises(RuntimeError, match="arrivals"):
        poller.run(
            settings,
            FlakyWriter(tmp_path),
            KEY,
            partial(TflClient, transport=httpx.MockTransport(tfl_handler)),
        )
    written = {p.parts[len(tmp_path.parts) + 1] for p in tmp_path.rglob("*.ndjson.gz")}
    assert {"line_status", "line_disruption"} <= written
    assert "arrivals" not in written


def test_handler_reads_key_from_ssm_and_writes_to_s3(
    aws: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    boto3.client("ssm", region_name="eu-west-2").put_parameter(
        Name="/mind-the-data/tfl/app_key", Value=KEY, Type="SecureString"
    )
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setenv("TFL_APP_KEY_PARAMETER", "/mind-the-data/tfl/app_key")
    monkeypatch.setenv("METRIC_NAMESPACE", "MindTheData")
    sent_keys: list[str | None] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        sent_keys.append(request.url.params.get("app_key"))
        return tfl_handler(request)

    monkeypatch.setattr(
        poller,
        "TflClient",
        partial(TflClient, transport=httpx.MockTransport(recording_handler)),
    )
    runtime.app_key.cache_clear()
    try:
        result = poller.handler({}, None)
    finally:
        runtime.app_key.cache_clear()

    assert set(sent_keys) == {KEY}
    assert result["objects_written"] >= 3
    out = capsys.readouterr().out
    assert KEY not in out
    emf = [json.loads(line) for line in out.splitlines() if '"_aws"' in line]
    assert {doc["Feed"] for doc in emf} >= {"line_status", "line_disruption", "arrivals"}
    assert all(doc["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "MindTheData" for doc in emf)
    assert next(d for d in emf if d["Feed"] == "arrivals")["RequestFailures"] == 1


def test_reference_fetch_requests_timetables_per_direction(aws: None) -> None:
    lines, stations = load_config()
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return tfl_handler(request)

    async def go() -> CollectionSummary:
        async with TflClient(KEY, transport=httpx.MockTransport(handler)) as client:
            return await reference.fetch_once(
                client,
                S3RawWriter(boto3.client("s3", region_name="eu-west-2"), BUCKET),
                lines,
                stations,
                budget_s=45,
                fetched_at=datetime(2026, 11, 9, 3, 0, tzinfo=UTC),
            )

    summary = asyncio.run(go())
    timetable_calls = [u for u in seen if "/Timetable/" in u.path]
    assert len(timetable_calls) == 2 * len(stations)
    assert {u.params["direction"] for u in timetable_calls} == {"inbound", "outbound"}
    assert summary.feeds[Feed.REF_ROUTE_SEQUENCE].requests == 2 * len(lines)
    assert summary.feeds[Feed.REF_TIMETABLE].invalid_records == 0
    feeds = {k.split("/")[1] for k in _objects()}
    assert feeds == {"ref_severity", "ref_route_sequence", "ref_timetable"}


@pytest.mark.parametrize(
    ("feed", "payload", "expected"),
    [
        (Feed.ARRIVALS, [{"id": "only-an-id"}], (0, 1)),
        (Feed.ARRIVALS, "not a list", (0, 1)),
        (Feed.ARRIVALS, [], (0, 0)),
        (Feed.REF_TIMETABLE, {}, (0, 1)),
    ],
)
def test_count_records_never_raises(feed: Feed, payload: Any, expected: tuple[int, int]) -> None:
    assert count_records(feed, payload) == expected


def test_count_records_keeps_valid_predictions_next_to_a_bad_one() -> None:
    good = _fixture("arrivals_940GZZLUOXC.json")
    assert count_records(Feed.ARRIVALS, [*good, {"id": "broken"}]) == (len(good), 1)


def test_local_poll_cli_writes_partitioned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    monkeypatch.setattr(
        poller, "TflClient", partial(TflClient, transport=httpx.MockTransport(tfl_handler))
    )
    # One station is served a 503: recorded as a failure envelope, and the run still succeeds.
    assert poller.main(["--local", "--out", str(tmp_path)]) == 0
    files = list(tmp_path.rglob("*.ndjson.gz"))
    assert {f.parts[len(tmp_path.parts) + 1] for f in files} >= {
        "line_status",
        "line_disruption",
        "arrivals",
    }
    assert all(f.parts[len(tmp_path.parts) + 2].startswith("dt=") for f in files)
