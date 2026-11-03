import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from mind_the_data.common.telemetry import configure_logging
from mind_the_data.ingest.client import (
    RequestResult,
    RetryPolicy,
    TflClient,
    _retry_after_s,
    display_path,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]],
    clock: FakeClock,
    *,
    app_key: str | None = "secret-key",
    max_attempts: int = 3,
) -> TflClient:
    return TflClient(
        app_key,
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=max_attempts, base_delay_s=0.5, max_delay_s=4.0),
        clock=clock,
        rand=lambda: 1.0,  # take the full jitter ceiling, so delays are predictable
        sleep=clock.sleep,
    )


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_success_returns_payload_and_strips_key_from_path() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=[{"id": "victoria"}])

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock) as client:
            return await client.get("/Line/Mode/tube/Status", deadline=clock.now + 45)

    result = run(go())
    assert result.ok
    assert result.payload == [{"id": "victoria"}]
    assert result.path == "/Line/Mode/tube/Status"
    assert result.attempts == 1
    assert seen[0].params["app_key"] == "secret-key"


def test_anonymous_sends_no_key() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=[])

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock, app_key=None) as client:
            return await client.get("/Line/Meta/Severity", deadline=clock.now + 45)

    run(go())
    assert "app_key" not in seen[0].params


def test_retries_5xx_with_backoff_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={})])
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: next(responses), clock) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert result.ok and result.attempts == 3
    assert clock.sleeps == [0.5, 1.0]


def test_gives_up_after_max_attempts_and_reports_last_status() -> None:
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: httpx.Response(500), clock) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert not result.ok
    assert result.http_status == 500
    assert result.error == "http_500"
    assert result.payload is None
    assert result.attempts == 3


def test_does_not_retry_client_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock) as client:
            return await client.get("/StopPoint/bad/Arrivals", deadline=clock.now + 45)

    result = run(go())
    assert calls == 1
    assert result.http_status == 404 and result.error == "http_404"


def test_honours_retry_after_on_429() -> None:
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json=[])]
    )
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: next(responses), clock) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert result.ok
    assert clock.sleeps == [3.0]


def test_timeout_has_null_status_and_error_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock, max_attempts=1) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert result.http_status is None
    assert result.error == "timeout"


def test_connection_error_is_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock, max_attempts=1) as client:
            return await client.get("/x", deadline=clock.now + 45)

    assert run(go()).error == "ConnectError"


def test_deadline_already_passed_records_budget_exhausted_without_calling() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock) as client:
            return await client.get("/x", deadline=clock.now - 1)

    result = run(go())
    assert calls == 0
    assert result.http_status is None and result.error == "budget_exhausted"


def test_no_retry_sleep_that_would_cross_the_deadline() -> None:
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: httpx.Response(503), clock) as client:
            return await client.get("/x", deadline=clock.now + 0.4)

    result = run(go())
    assert clock.sleeps == []  # the 0.5 s backoff would pass the 0.4 s budget
    assert result.attempts == 1 and result.http_status == 503


def test_non_json_success_keeps_text_and_flags_it() -> None:
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: httpx.Response(200, text="<html>"), clock) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert result.payload == "<html>"
    assert result.error == "invalid_json"


def test_get_many_keeps_order_and_bounds_concurrency() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        in_flight -= 1
        return httpx.Response(200, json={"path": request.url.path})

    clock = FakeClock()
    paths = [f"/StopPoint/{i}/Arrivals" for i in range(25)]

    async def go() -> list[RequestResult]:
        client = TflClient(
            None,
            transport=httpx.MockTransport(handler),
            max_concurrency=4,
            clock=clock,
            sleep=clock.sleep,
        )
        async with client:
            return await client.get_many([(p, None) for p in paths], deadline=clock.now + 45)

    results = run(go())
    assert [r.payload["path"] for r in results] == paths
    assert peak <= 4


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (None, "/Line/victoria/Timetable/940GZZLUOXC"),
        ({"app_key": "k"}, "/Line/victoria/Timetable/940GZZLUOXC"),
        (
            {"direction": "inbound", "APP_KEY": "k"},
            "/Line/victoria/Timetable/940GZZLUOXC?direction=inbound",
        ),
        ({"b": "2", "a": "1"}, "/Line/victoria/Timetable/940GZZLUOXC?a=1&b=2"),
    ],
)
def test_display_path_never_contains_the_key(params: dict[str, str] | None, expected: str) -> None:
    assert display_path("/Line/victoria/Timetable/940GZZLUOXC", params) == expected


KEY = "0f" * 16


def test_key_never_reaches_logs_results_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("DEBUG")
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: httpx.Response(200, json=[]), clock, app_key=KEY) as client:
            return await client.get(
                "/Line/Mode/tube/Status", {"detail": "true"}, deadline=clock.now + 45
            )

    with caplog.at_level(logging.DEBUG):
        result = run(go())
    assert KEY not in caplog.text
    assert KEY not in result.path
    assert KEY not in repr(result)


def test_query_string_in_path_is_rejected() -> None:
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(lambda r: httpx.Response(200), clock) as client:
            return await client.get("/x?app_key=" + KEY, deadline=clock.now + 45)

    with pytest.raises(ValueError, match="params"):
        run(go())


def test_slow_response_is_cut_off_at_the_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=[])

    clock = FakeClock()  # frozen, so only the real 0.05 s budget can end the wait

    async def go() -> RequestResult:
        async with _client(handler, clock) as client:
            return await client.get("/x", deadline=clock.now + 0.05)

    started = time.monotonic()
    result = run(go())
    assert time.monotonic() - started < 1
    assert result.http_status is None and result.error == "budget_exhausted"


def test_deadline_passing_while_queued_for_a_slot() -> None:
    release = asyncio.Event()
    clock = FakeClock()

    async def handler(request: httpx.Request) -> httpx.Response:
        await release.wait()
        clock.now += 50  # the first request holds the only slot past the deadline
        return httpx.Response(200, json=[])

    async def go() -> list[RequestResult]:
        client = TflClient(
            None, transport=httpx.MockTransport(handler), max_concurrency=1, clock=clock
        )
        async with client:
            task = asyncio.create_task(
                client.get_many([("/a", None), ("/b", None)], deadline=clock.now + 45)
            )
            await asyncio.sleep(0.01)
            release.set()
            return await task

    first, second = run(go())
    assert first.ok
    assert second.error == "budget_exhausted" and second.attempts == 0


def test_retry_after_longer_than_the_budget_stops_retrying() -> None:
    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(
            lambda r: httpx.Response(429, headers={"Retry-After": "60"}), clock
        ) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert clock.sleeps == []
    assert result.http_status == 429 and result.attempts == 1


def test_retry_after_as_http_date() -> None:
    response = httpx.Response(429, headers={"Retry-After": "Sun, 08 Nov 2026 08:15:10 GMT"})
    now = datetime(2026, 11, 8, 8, 15, 4, tzinfo=UTC)
    assert _retry_after_s(response, now) == 6.0


def test_transport_error_is_retried_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json=[])

    clock = FakeClock()

    async def go() -> RequestResult:
        async with _client(handler, clock) as client:
            return await client.get("/x", deadline=clock.now + 45)

    result = run(go())
    assert result.ok and result.attempts == 2


def test_unexpected_exception_becomes_a_failure_and_spares_the_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            raise RuntimeError("bug with " + KEY)
        if request.url.path == "/garbled":
            raise httpx.DecodingError("bad gzip")
        return httpx.Response(200, json=[])

    clock = FakeClock()

    async def go() -> list[RequestResult]:
        async with _client(handler, clock, max_attempts=1) as client:
            return await client.get_many(
                [("/ok", None), ("/bad", None), ("/garbled", None)], deadline=clock.now + 45
            )

    ok, bad, garbled = run(go())
    assert ok.ok
    assert bad.error == "unexpected:RuntimeError" and bad.http_status is None
    assert garbled.error == "DecodingError"
    assert KEY not in repr([ok, bad, garbled])


def test_concurrency_never_exceeds_the_limit_while_requests_are_held() -> None:
    gate = asyncio.Event()
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await gate.wait()
        in_flight -= 1
        return httpx.Response(200, json=[])

    clock = FakeClock()

    async def go() -> list[RequestResult]:
        client = TflClient(
            None, transport=httpx.MockTransport(handler), max_concurrency=3, clock=clock
        )
        async with client:
            task = asyncio.create_task(
                client.get_many([(f"/s/{i}", None) for i in range(10)], deadline=clock.now + 45)
            )
            await asyncio.sleep(0.02)
            held = in_flight
            gate.set()
            results = await task
            assert held == 3
            return results

    assert all(r.ok for r in run(go()))
    assert peak == 3
