"""Poller and reference collection end to end: recorded TfL fixtures over a
fake transport, the real config, and moto S3 and SSM. No network."""

import asyncio
import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws

from mind_the_data.common.config import distinct_station_ids, load_config
from mind_the_data.common.raw import Envelope, Feed, decode_ndjson_gz
from mind_the_data.common.writer import LocalRawWriter, S3RawWriter
from mind_the_data.ingest import poller, reference, runtime
from mind_the_data.ingest.client import RetryPolicy, TflClient
from mind_the_data.ingest.collect import (
    CollectionFailed,
    PlannedRequest,
    count_records,
    run_collection,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tfl"
BUCKET = "mind-the-data-raw-123456789012"
KEY = "0f" * 16
FAILING_STATION = "940GZZLUBNK"
FAILING_LINE = "central"
MONDAY = datetime(2026, 11, 16, tzinfo=UTC).date()
TUESDAY = datetime(2026, 11, 17, tzinfo=UTC).date()
SETTINGS = runtime.Settings.from_env({"METRIC_NAMESPACE": "MindTheData"})


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
    if path == f"/Line/{FAILING_LINE}/Arrivals":
        return httpx.Response(503)
    if path.startswith("/Line/") and path.endswith("/Arrivals"):
        file = FIXTURES / f"line_arrivals_{path.split('/')[2]}.json"
        return httpx.Response(200, json=_fixture(file.name) if file.exists() else [])
    if path.endswith("/Arrivals"):
        file = FIXTURES / f"arrivals_{path.split('/')[2]}.json"
        return httpx.Response(200, json=_fixture(file.name) if file.exists() else [])
    if path == "/Line/Meta/Severity":
        return httpx.Response(200, json=_fixture("severity.json"))
    if "/Route/Sequence/" in path:
        return httpx.Response(200, json=_fixture("route_sequence_victoria_inbound.json"))
    if "/Timetable/" in path:
        direction = request.url.params.get("direction")
        if direction is None:
            return httpx.Response(200, json=_fixture("timetable_victoria_940GZZLUOXC.json"))
        return httpx.Response(
            200, json=_fixture(f"timetable_victoria_940GZZLUOXC_{direction}.json")
        )
    return httpx.Response(404)


def factory(
    handler: Callable[[httpx.Request], httpx.Response] = tfl_handler,
) -> Callable[..., TflClient]:
    # One attempt, so failures are immediate and the suite stays fast.
    return partial(
        TflClient, transport=httpx.MockTransport(handler), retry=RetryPolicy(max_attempts=1)
    )


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3").create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
        )
        yield


def s3_writer() -> S3RawWriter:
    return S3RawWriter(boto3.client("s3"), BUCKET)


def objects() -> dict[str, list[Envelope]]:
    s3 = boto3.client("s3")
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/").get("Contents", [])
    return {
        o["Key"]: decode_ndjson_gz(s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
        for o in listing
    }


def feeds_written(keys: Any) -> set[str]:
    return {key.split("/")[1] for key in keys}


def emf_lines(out: str) -> dict[str, dict[str, Any]]:
    docs = [json.loads(line) for line in out.splitlines() if '"_aws"' in line]
    return {doc["Feed"]: doc for doc in docs}


SLOT_PLAIN = datetime(2026, 11, 8, 8, 16, tzinfo=UTC)
SLOT_FIVE = datetime(2026, 11, 8, 8, 20, tzinfo=UTC)


# ---- Poller ------------------------------------------------------------------


def test_poll_writes_one_object_per_feed_including_failures(aws: None) -> None:
    summary = poller.run(SETTINGS, s3_writer(), KEY, slot=SLOT_PLAIN, client_factory=factory())

    written = objects()
    assert feeds_written(written) == {"line_status", "line_disruption", "arrivals", "line_arrivals"}
    (arrivals,) = (v for k, v in written.items() if k.startswith("raw/arrivals/"))
    lines, stations = load_config()
    assert len(arrivals) == len(distinct_station_ids(stations))
    assert len({e.poll_id for envs in written.values() for e in envs}) == 1

    failed = [e for e in arrivals if not e.ok]
    assert [e.request.path for e in failed] == [f"/StopPoint/{FAILING_STATION}/Arrivals"]
    assert failed[0].http_status == 503 and failed[0].payload is None
    assert summary.feeds[Feed.ARRIVALS].failures == 1
    assert summary.feeds[Feed.LINE_STATUS].records == 20

    ((key, line_boards),) = (
        (k, v) for k, v in written.items() if k.startswith("raw/line_arrivals/")
    )
    assert re.fullmatch(
        r"raw/line_arrivals/dt=\d{4}-\d{2}-\d{2}/hour=\d{2}/"
        r"line_arrivals_\d{8}T\d{6}Z_[0-9a-f-]{36}\.ndjson\.gz",
        key,
    )
    assert [e.request.path for e in line_boards] == [f"/Line/{line.id}/Arrivals" for line in lines]
    assert {e.feed for e in line_boards} == {Feed.LINE_ARRIVALS}
    failed_lines = [e.request.path for e in line_boards if not e.ok]
    assert failed_lines == [f"/Line/{FAILING_LINE}/Arrivals"]
    outcome = summary.feeds[Feed.LINE_ARRIVALS]
    assert (outcome.requests, outcome.failures, outcome.invalid_records) == (20, 1, 0)
    expected = sum(
        len(_fixture(f"line_arrivals_{line}.json")) for line in ("bakerloo", "elizabeth", "liberty")
    )
    assert outcome.records == expected > 0


def test_stop_disruption_follows_the_scheduled_minute(aws: None) -> None:
    poller.run(SETTINGS, s3_writer(), KEY, slot=SLOT_FIVE, client_factory=factory())
    assert "stop_disruption" in feeds_written(objects())


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"scheduled_time": "2026-11-08T08:20:00Z"}, SLOT_FIVE),
        ({"scheduled_time": "not a time"}, SLOT_PLAIN),
        ({}, SLOT_PLAIN),
    ],
)
def test_scheduled_slot(event: dict[str, Any], expected: datetime) -> None:
    assert poller.scheduled_slot(event, SLOT_PLAIN) == expected


def test_key_is_never_written_to_raw(aws: None) -> None:
    poller.run(SETTINGS, s3_writer(), KEY, slot=SLOT_FIVE, client_factory=factory())
    for envelopes in objects().values():
        for env in envelopes:
            assert KEY not in env.model_dump_json()


def test_write_failure_keeps_other_feeds_and_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class FlakyWriter(LocalRawWriter):
        def write(self, feed: Feed, *args: Any, **kwargs: Any) -> str:
            if feed is Feed.ARRIVALS:
                raise OSError("disk full")
            return super().write(feed, *args, **kwargs)

    with pytest.raises(CollectionFailed, match="raw write failed for arrivals"):
        poller.run(SETTINGS, FlakyWriter(tmp_path), KEY, slot=SLOT_PLAIN, client_factory=factory())

    written = {p.relative_to(tmp_path).parts[1] for p in tmp_path.rglob("*.ndjson.gz")}
    assert written == {"line_status", "line_disruption", "line_arrivals"}
    emf = emf_lines(capsys.readouterr().out)
    assert emf["arrivals"]["ObjectsWritten"] == 0
    assert emf["line_status"]["ObjectsWritten"] == 1


def test_total_outage_writes_failures_then_fails_and_drops_the_cached_key(aws: None) -> None:
    dropped: list[bool] = []

    with pytest.raises(CollectionFailed, match="every request failed"):
        run_collection(
            [
                PlannedRequest(Feed.LINE_STATUS, "/Line/Mode/tube/Status"),
                PlannedRequest(Feed.ARRIVALS, "/StopPoint/940GZZLUOXC/Arrivals"),
            ],
            writer=s3_writer(),
            app_key=KEY,
            polled_at=SLOT_PLAIN,
            budget_s=45,
            max_concurrency=10,
            namespace="MindTheData",
            message="poll complete",
            client_factory=factory(lambda r: httpx.Response(403)),
            on_auth_failure=lambda: dropped.append(True),
        )

    written = objects()
    assert feeds_written(written) == {"line_status", "arrivals"}
    assert all(e.http_status == 403 for envs in written.values() for e in envs)
    assert dropped == [True]


def test_budget_exhaustion_is_written_as_a_failure_envelope(tmp_path: Path) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=[])

    with pytest.raises(CollectionFailed):
        run_collection(
            [PlannedRequest(Feed.LINE_STATUS, "/Line/Mode/tube/Status")],
            writer=LocalRawWriter(tmp_path),
            app_key=None,
            polled_at=SLOT_PLAIN,
            budget_s=0.05,
            max_concurrency=1,
            namespace="MindTheData",
            message="poll complete",
            client_factory=partial(TflClient, transport=httpx.MockTransport(slow)),
        )
    (path,) = tmp_path.rglob("*.ndjson.gz")
    (env,) = decode_ndjson_gz(path.read_bytes())
    assert env.http_status is None and env.error == "budget_exhausted"


def test_handler_reads_key_from_ssm_writes_to_s3_and_emits_metrics(
    aws: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    boto3.client("ssm").put_parameter(
        Name="/mind-the-data/tfl/app_key", Value=KEY, Type="SecureString"
    )
    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setenv("TFL_APP_KEY_PARAMETER", "/mind-the-data/tfl/app_key")
    sent_keys: list[str | None] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent_keys.append(request.url.params.get("app_key"))
        return tfl_handler(request)

    monkeypatch.setattr(poller, "TflClient", factory(recording))
    runtime.app_key.cache_clear()
    try:
        result = poller.handler({"scheduled_time": "2026-11-08T08:20:00Z"}, None)
    finally:
        runtime.app_key.cache_clear()

    assert set(sent_keys) == {KEY}
    assert result["objects_written"] == 5
    assert result["feeds"]["line_arrivals"]["requests"] == 20
    out = capsys.readouterr().out
    assert KEY not in out
    emf = emf_lines(out)
    assert set(emf) == {
        "line_status",
        "line_disruption",
        "arrivals",
        "line_arrivals",
        "stop_disruption",
    }
    assert emf["arrivals"]["RequestFailures"] == 1
    assert emf["line_arrivals"]["RequestFailures"] == 1
    assert emf["line_arrivals"]["ObjectsWritten"] == 1


def test_local_poll_cli_writes_partitioned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    monkeypatch.setattr(poller, "TflClient", factory())
    # One station is served a 503: a failure envelope, and the run still succeeds.
    assert poller.main(["--local", "--out", str(tmp_path)]) == 0
    files = list(tmp_path.rglob("*.ndjson.gz"))
    assert {f.relative_to(tmp_path).parts[1] for f in files} >= {
        "line_status",
        "line_disruption",
        "arrivals",
    }
    assert all(f.relative_to(tmp_path).parts[2].startswith("dt=") for f in files)


def test_line_board_failures_never_stop_the_other_feeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every line-wide request failing, and its write failing, still leaves the rest written."""

    def line_boards_down(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Line/") and path.endswith("/Arrivals"):
            return httpx.Response(500)
        return tfl_handler(request)

    with pytest.raises(CollectionFailed, match="every request failed for line_arrivals"):
        poller.run(
            SETTINGS,
            LocalRawWriter(tmp_path),
            KEY,
            slot=SLOT_FIVE,
            client_factory=factory(line_boards_down),
        )
    written = {p.relative_to(tmp_path).parts[1] for p in tmp_path.rglob("*.ndjson.gz")}
    assert written == {
        "line_status",
        "line_disruption",
        "arrivals",
        "line_arrivals",
        "stop_disruption",
    }
    (line_file,) = tmp_path.glob("raw/line_arrivals/*/*/*.ndjson.gz")
    assert all(e.http_status == 500 for e in decode_ndjson_gz(line_file.read_bytes()))

    class LineWriteFails(LocalRawWriter):
        def write(self, feed: Feed, *args: Any, **kwargs: Any) -> str:
            if feed is Feed.LINE_ARRIVALS:
                raise OSError("disk full")
            return super().write(feed, *args, **kwargs)

    out = tmp_path / "second"
    with pytest.raises(CollectionFailed, match="raw write failed for line_arrivals"):
        poller.run(SETTINGS, LineWriteFails(out), KEY, slot=SLOT_PLAIN, client_factory=factory())
    written = {p.relative_to(out).parts[1] for p in out.rglob("*.ndjson.gz")}
    assert written == {"line_status", "line_disruption", "arrivals"}
    emf = emf_lines(capsys.readouterr().out)
    assert emf["line_arrivals"]["ObjectsWritten"] == 0
    assert emf["arrivals"]["ObjectsWritten"] == 1


def test_slow_line_boards_are_cut_at_the_budget_and_station_boards_still_land(
    tmp_path: Path,
) -> None:
    async def slow_lines(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Line/") and path.endswith("/Arrivals"):
            await asyncio.sleep(5)
        return tfl_handler(request)

    with pytest.raises(CollectionFailed, match="every request failed for line_arrivals"):
        poller.run(
            SETTINGS,
            LocalRawWriter(tmp_path),
            KEY,
            budget_s=0.5,
            slot=SLOT_PLAIN,
            client_factory=partial(
                TflClient,
                transport=httpx.MockTransport(slow_lines),
                retry=RetryPolicy(max_attempts=1),
            ),
        )
    (line_file,) = tmp_path.glob("raw/line_arrivals/*/*/*.ndjson.gz")
    assert {e.error for e in decode_ndjson_gz(line_file.read_bytes())} == {"budget_exhausted"}
    # Queued ahead of the line boards, every station board but the one the
    # fake fails was answered.
    (station_file,) = tmp_path.glob("raw/arrivals/*/*/*.ndjson.gz")
    stations = decode_ndjson_gz(station_file.read_bytes())
    assert [e.request.path for e in stations if not e.ok] == [
        f"/StopPoint/{FAILING_STATION}/Arrivals"
    ]


# ---- Reference ---------------------------------------------------------------


def _recording() -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return tfl_handler(request)

    return seen, handler


def test_monday_reference_fetches_each_measurement_timetable_per_direction(aws: None) -> None:
    lines, stations = load_config()
    seen, handler = _recording()

    summary = reference.run(SETTINGS, s3_writer(), KEY, day=MONDAY, client_factory=factory(handler))

    timetable_calls = [u for u in seen if "/Timetable/" in u.path]
    assert len(timetable_calls) == 2 * len(stations)
    assert {u.params["direction"] for u in timetable_calls} == {"inbound", "outbound"}
    assert summary.feeds[Feed.REF_ROUTE_SEQUENCE].requests == 2 * len(lines)
    assert summary.feeds[Feed.REF_TIMETABLE].invalid_records == 0
    assert feeds_written(objects()) == {"ref_severity", "ref_route_sequence", "ref_timetable"}


def test_other_days_fetch_that_days_network_share_after_the_route_sequences(aws: None) -> None:
    lines, stations = load_config()
    seen, handler = _recording()

    summary = reference.run(
        SETTINGS, s3_writer(), KEY, day=TUESDAY, client_factory=factory(handler)
    )

    # The fake serves Victoria's route sequence for every line, so each line
    # with timetables "has" Victoria's 16 stations.
    victoria = {
        n
        for r in _fixture("route_sequence_victoria_inbound.json")["orderedLineRoutes"]
        for n in r["naptanIds"]
    }
    measured = {(s.line_id, s.naptan_id) for s in stations}
    expected = {
        (line.id, naptan, direction)
        for line in lines
        if line.mode in reference.NETWORK_TIMETABLE_MODES
        for naptan in victoria
        if (line.id, naptan) not in measured and reference.network_weekday(line.id, naptan) == 1
        for direction in reference.DIRECTIONS
    }
    timetable_calls = [u for u in seen if "/Timetable/" in u.path]
    got = {
        (u.path.split("/")[2], u.path.split("/")[4], u.params["direction"]) for u in timetable_calls
    }
    assert got == expected and len(timetable_calls) == len(expected) > 0
    # Timetables are planned only once every route sequence has answered.
    first_timetable = next(i for i, u in enumerate(seen) if "/Timetable/" in u.path)
    assert sum("/Route/Sequence/" in u.path for u in seen[:first_timetable]) == 2 * len(lines)
    assert summary.feeds[Feed.REF_TIMETABLE].requests == len(expected)

    written = objects()
    assert feeds_written(written) == {"ref_severity", "ref_route_sequence", "ref_timetable"}
    assert len({e.poll_id for envs in written.values() for e in envs}) == 1


def test_reference_handler_takes_its_day_from_the_schedule(
    aws: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setenv("TFL_APP_KEY", KEY)
    monkeypatch.delenv("TFL_APP_KEY_PARAMETER", raising=False)
    monkeypatch.setattr(reference, "TflClient", factory())
    runtime.app_key.cache_clear()
    try:
        result = reference.handler({"scheduled_time": "2026-11-16T03:00:00Z"}, None)
    finally:
        runtime.app_key.cache_clear()
    _, stations = load_config()
    assert result["feeds"]["ref_timetable"]["requests"] == 2 * len(stations)


def test_follow_up_planning_failure_keeps_the_first_round_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> Any:
        raise KeyError("bug")

    monkeypatch.setattr(reference, "plan_network_timetables", broken)
    with pytest.raises(CollectionFailed, match="follow-up planning failed: KeyError"):
        reference.run(
            SETTINGS, LocalRawWriter(tmp_path), KEY, day=TUESDAY, client_factory=factory()
        )
    written = {p.relative_to(tmp_path).parts[1] for p in tmp_path.rglob("*.ndjson.gz")}
    assert written == {"ref_severity", "ref_route_sequence"}


def test_reference_write_failure_fails_the_run(tmp_path: Path) -> None:
    class FlakyWriter(LocalRawWriter):
        def write(self, feed: Feed, *args: Any, **kwargs: Any) -> str:
            if feed is Feed.REF_TIMETABLE:
                raise OSError("disk full")
            return super().write(feed, *args, **kwargs)

    with pytest.raises(CollectionFailed, match="ref_timetable"):
        reference.run(SETTINGS, FlakyWriter(tmp_path), KEY, day=MONDAY, client_factory=factory())
    written = {p.relative_to(tmp_path).parts[1] for p in tmp_path.rglob("*.ndjson.gz")}
    assert written == {"ref_severity", "ref_route_sequence"}


# ---- Validation counts and settings ------------------------------------------


@pytest.mark.parametrize(
    ("feed", "payload", "expected"),
    [
        (Feed.ARRIVALS, [{"id": "only-an-id"}], (0, 1)),
        (Feed.ARRIVALS, "not a list", (0, 1)),
        (Feed.ARRIVALS, [], (0, 0)),
        (Feed.REF_TIMETABLE, {}, (0, 1)),
        (Feed.REF_TIMETABLE, "<html>", (0, 1)),
    ],
)
def test_count_records_never_raises(feed: Feed, payload: Any, expected: tuple[int, int]) -> None:
    assert count_records(feed, payload) == expected


def test_disambiguation_counts_as_an_invalid_timetable() -> None:
    assert count_records(Feed.REF_TIMETABLE, _fixture("timetable_victoria_940GZZLUOXC.json")) == (
        0,
        1,
    )
    assert count_records(
        Feed.REF_TIMETABLE, _fixture("timetable_victoria_940GZZLUOXC_inbound.json")
    ) == (1, 0)


def test_valid_predictions_count_next_to_a_bad_one() -> None:
    good = _fixture("arrivals_940GZZLUOXC.json")
    assert count_records(Feed.ARRIVALS, [*good, {"id": "broken"}]) == (len(good), 1)


class FakeContext:
    def __init__(self, remaining_ms: int) -> None:
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


@pytest.mark.parametrize(
    ("remaining_ms", "expected"),
    [(60_000, 45.0), (40_000, 30.0), (5_000, 1.0)],
)
def test_budget_is_capped_by_lambda_time_left(remaining_ms: int, expected: float) -> None:
    assert runtime.budget_for(SETTINGS, FakeContext(remaining_ms)) == expected
    assert runtime.budget_for(SETTINGS, None) == 45.0


@pytest.mark.parametrize(
    "env",
    [{"POLL_BUDGET_SECONDS": "0"}, {"POLL_BUDGET_SECONDS": "900"}, {"MAX_CONCURRENCY": "0"}],
)
def test_settings_reject_unsafe_values(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        runtime.Settings.from_env(env)
