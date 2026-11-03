import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest

from mind_the_data.ingest.client import RequestResult, RetryPolicy, TflClient, display_path


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
    handler: Callable[[httpx.Request], httpx.Response],
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
