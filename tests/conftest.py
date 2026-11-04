"""Test-wide guards: tests never reach the network or real AWS.

Real HTTP transports are disabled, so a test that forgets to inject a fake
transport fails loudly instead of calling the live TfL API. AWS credentials
and config are replaced with dummies, so a test that forgets moto cannot use
a developer's real account.
"""

from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def refuse_async(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        raise RuntimeError(f"network disabled in tests: {request.method} {request.url.host}")

    def refuse_sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"network disabled in tests: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_sync)
    yield


@pytest.fixture(autouse=True)
def _fake_aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    yield
