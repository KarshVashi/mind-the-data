"""Async TfL Unified API client.

Bounded concurrency, retries with exponential backoff and full jitter, and a
hard poll budget: once the deadline passes, nothing new starts and anything
unfinished is recorded as a failure rather than overrunning into the next
minute's poll. Every request yields a RequestResult, success or not, so the
caller can write a failure envelope instead of silently dropping data.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tfl.gov.uk"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RequestResult:
    """One logical request after retries. ``path`` never contains the key."""

    path: str
    http_status: int | None
    latency_ms: int | None
    error: str | None
    payload: Any
    attempts: int

    @property
    def ok(self) -> bool:
        return self.http_status is not None and 200 <= self.http_status < 300 and self.error is None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 4.0

    def delay(self, attempt: int, rand: Callable[[], float]) -> float:
        """Full jitter: uniform in [0, min(cap, base * 2**(attempt-1))]."""
        ceiling = min(self.max_delay_s, self.base_delay_s * 2.0 ** (attempt - 1))
        return ceiling * rand()


def display_path(path: str, params: Mapping[str, str] | None = None) -> str:
    """Path plus sorted query string, with any app key removed."""
    clean = {k: v for k, v in (params or {}).items() if k.lower() not in {"app_key", "app_id"}}
    if not clean:
        return path
    return f"{path}?{urlencode(sorted(clean.items()))}"


def _retry_after_s(response: httpx.Response, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header: delta-seconds or an HTTP date."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class TflClient:
    def __init__(
        self,
        app_key: str | None,
        *,
        base_url: str = BASE_URL,
        max_concurrency: int = 10,
        request_timeout_s: float = 10.0,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._app_key = app_key
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout_s = request_timeout_s
        self._retry = retry or RetryPolicy()
        self._clock = clock
        self._rand = rand
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "mind-the-data/0.1"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self, path: str, params: Mapping[str, str] | None = None, *, deadline: float
    ) -> RequestResult:
        """GET ``path`` with retries, never running past ``deadline`` (monotonic seconds)."""
        if "?" in path:
            raise ValueError("pass query parameters via params, not in the path")
        shown = display_path(path, params)
        query = dict(params or {})
        if self._app_key:
            query["app_key"] = self._app_key

        async with self._semaphore:
            attempt = 0
            # latency_ms runs from the first attempt (after any queueing for a
            # connection slot) to the final result, including retries.
            started = self._clock()
            last_status: int | None = None
            last_error: str | None = "budget_exhausted"
            payload: Any = None
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return self._result(shown, last_status, started, last_error, payload, attempt)
                attempt += 1
                retry_after: float | None = None
                try:
                    # httpx timeouts apply per phase (connect, each read), so a
                    # trickling response could outlive them; this bounds the
                    # whole attempt by what is left of the budget.
                    async with asyncio.timeout(remaining):
                        response = await self._http.get(
                            path, params=query, timeout=min(self._timeout_s, remaining)
                        )
                except TimeoutError:
                    return self._result(shown, None, started, "budget_exhausted", None, attempt)
                except httpx.TimeoutException:
                    last_status, last_error, payload = None, "timeout", None
                except httpx.HTTPError as exc:
                    # Type name only: httpx messages can include the URL, and
                    # with it the app key.
                    last_status, last_error, payload = None, type(exc).__name__, None
                except Exception as exc:  # noqa: BLE001 - must become a failure record
                    # Anything else must still become a failure record, not an
                    # exception that would lose the whole batch.
                    error = f"unexpected:{type(exc).__name__}"
                    return self._result(shown, None, started, error, None, attempt)
                else:
                    last_status = response.status_code
                    payload, last_error = _decode(response)
                    if response.status_code not in RETRY_STATUSES:
                        return self._result(
                            shown, last_status, started, last_error, payload, attempt
                        )
                    retry_after = _retry_after_s(response)

                if attempt >= self._retry.max_attempts:
                    return self._result(shown, last_status, started, last_error, payload, attempt)
                wait = self._retry.delay(attempt, self._rand)
                if retry_after is not None:
                    wait = max(wait, retry_after)
                if self._clock() + wait >= deadline:
                    return self._result(shown, last_status, started, last_error, payload, attempt)
                await self._sleep(wait)

    async def get_many(
        self, requests: Sequence[tuple[str, Mapping[str, str] | None]], *, deadline: float
    ) -> list[RequestResult]:
        """Run requests concurrently (bounded); results keep the input order.

        get() records its own failures, so an exception here is a bug; it is
        still turned into a failure result so the other requests survive.
        """
        outcomes = await asyncio.gather(
            *(self.get(path, params, deadline=deadline) for path, params in requests),
            return_exceptions=True,
        )
        results: list[RequestResult] = []
        for (path, params), outcome in zip(requests, outcomes, strict=True):
            if isinstance(outcome, RequestResult):
                results.append(outcome)
            elif isinstance(outcome, Exception):
                results.append(
                    RequestResult(
                        path=display_path(path, params),
                        http_status=None,
                        latency_ms=None,
                        error=f"unexpected:{type(outcome).__name__}",
                        payload=None,
                        attempts=0,
                    )
                )
            else:
                raise outcome  # cancellation and other BaseExceptions propagate
        return results

    def _result(
        self,
        path: str,
        status: int | None,
        started: float,
        error: str | None,
        payload: Any,
        attempts: int,
    ) -> RequestResult:
        latency = round((self._clock() - started) * 1000)
        return RequestResult(
            path=path,
            http_status=status,
            latency_ms=max(latency, 0),
            error=error,
            payload=payload,
            attempts=attempts,
        )


def _decode(response: httpx.Response) -> tuple[Any, str | None]:
    """Return (payload, error). Non-2xx keeps no payload; bad JSON keeps the text."""
    if not 200 <= response.status_code < 300:
        return None, f"http_{response.status_code}"
    try:
        return response.json(), None
    except ValueError:
        return response.text, "invalid_json"
