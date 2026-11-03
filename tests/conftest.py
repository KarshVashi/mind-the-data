"""Test-wide guard: tests never reach the network.

Real HTTP transports are disabled, so a test that forgets to inject a fake
transport fails loudly instead of calling the live TfL API.
"""

from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def refuse(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"network disabled in tests: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    yield
