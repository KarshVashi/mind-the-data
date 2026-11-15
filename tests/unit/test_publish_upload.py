"""R2 upload and deploy hook: layout, headers, order, and settings handling."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from mind_the_data.publish import upload
from mind_the_data.publish.open_data import DATASETS
from mind_the_data.publish.upload import (
    CARD_CACHE,
    JSON,
    MARKDOWN,
    OPEN_DATA_CACHE,
    OPEN_DATASETS,
    PARQUET,
    PNG,
    SHORT_CACHE,
    WEEK_CACHE,
    R2Settings,
    UploadError,
    call_deploy_hook,
    classify,
    plan_uploads,
)

ACCOUNT = "0123456789abcdef0123456789abcdef"
ENDPOINT = f"https://{ACCOUNT}.r2.cloudflarestorage.com"
HOOK = "https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/not-a-real-hook"
ENV = {
    "R2_ACCOUNT_ID": ACCOUNT,
    "R2_ACCESS_KEY_ID": "test-key-id",
    "R2_SECRET_ACCESS_KEY": "test-secret",
    "R2_BUCKET": "mind-the-data-public",
    "PAGES_DEPLOY_HOOK_URL": HOOK,
}
EXPORT = (
    "league/2026-W45.json",
    "league/2026-W46.json",
    "league/latest.json",
    "lines/bakerloo.json",
    "lines/hammersmith-city.json",
    "meta/index.json",
    "meta/methodology.json",
    "cards/2026-W46.png",
    "cards/2026-W46-1080x1350.png",
    "divisions/2026-11.json",
    "season/2026-27.json",
)


def export_dir(root: Path, files: tuple[str, ...] = EXPORT) -> Path:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG" if name.endswith(".png") else b'{"k": 1}\n')
    return root


@pytest.mark.parametrize(
    ("key", "content_type", "cache"),
    [
        ("league/latest.json", JSON, SHORT_CACHE),
        ("league/2026-W46.json", JSON, WEEK_CACHE),
        ("lines/waterloo-city.json", JSON, SHORT_CACHE),
        ("meta/index.json", JSON, SHORT_CACHE),
        ("meta/methodology.json", JSON, SHORT_CACHE),
        ("cards/2026-W46.png", PNG, CARD_CACHE),
        ("cards/2026-W46-1080x1350.png", PNG, CARD_CACHE),
        ("divisions/2026-11.json", JSON, SHORT_CACHE),
        ("season/2026-27.json", JSON, SHORT_CACHE),
    ],
)
def test_classify_follows_the_site_integration_table(
    key: str, content_type: str, cache: str
) -> None:
    assert classify(key) == (content_type, cache)


@pytest.mark.parametrize(
    "key",
    [
        ".DS_Store",
        "league/2026-W46.json.bak",
        "league/W46.json",
        "lines/Bakerloo.json",
        "cards/card.png",
        "meta/secrets.json",
        "numbers.md",
        "open-data/2026-11/lines.parquet",
        "divisions/2026-13.json",
        "season/2026.json",
    ],
)
def test_classify_refuses_anything_else(key: str) -> None:
    with pytest.raises(UploadError, match="not a published file"):
        classify(key)


def test_plan_puts_latest_then_index_last(tmp_path: Path) -> None:
    keys = [p.key for p in plan_uploads(export_dir(tmp_path))]
    assert sorted(keys) == sorted(EXPORT)
    assert keys[-2:] == ["league/latest.json", "meta/index.json"]


def test_plan_refuses_an_export_without_the_index(tmp_path: Path) -> None:
    files = tuple(f for f in EXPORT if f != "meta/index.json")
    with pytest.raises(UploadError, match=r"missing: meta/index\.json"):
        plan_uploads(export_dir(tmp_path, files))


def test_plan_refuses_a_stray_file(tmp_path: Path) -> None:
    export_dir(tmp_path)
    (tmp_path / "numbers.md").write_text("draft")
    with pytest.raises(UploadError, match="numbers.md"):
        plan_uploads(tmp_path)


OPEN_DATA_EXPORT = (
    "open-data/2026-11/line_minute_status-000.parquet",
    "open-data/2026-11/line_minute_status-001.parquet",
    "open-data/2026-11/line_daily.parquet",
    "open-data/2026-11/line_weekly.parquet",
    "open-data/2026-11/README.md",
    "open-data/2026-11/manifest.json",
)


@pytest.mark.parametrize(
    ("key", "content_type"),
    [
        ("open-data/2026-11/line_daily.parquet", PARQUET),
        ("open-data/2026-11/line_minute_status-012.parquet", PARQUET),
        ("open-data/2026-11/README.md", MARKDOWN),
        ("open-data/2026-11/manifest.json", JSON),
    ],
)
def test_classify_open_data(key: str, content_type: str) -> None:
    assert classify(key) == (content_type, OPEN_DATA_CACHE)


def test_upload_knows_every_open_dataset() -> None:
    assert OPEN_DATASETS == tuple(d.name for d in DATASETS)


def test_plan_puts_the_open_data_manifest_last(tmp_path: Path) -> None:
    keys = [p.key for p in plan_uploads(export_dir(tmp_path, OPEN_DATA_EXPORT))]
    assert sorted(keys) == sorted(OPEN_DATA_EXPORT)
    assert keys[-1] == "open-data/2026-11/manifest.json"


def test_plan_refuses_open_data_without_its_readme(tmp_path: Path) -> None:
    files = tuple(f for f in OPEN_DATA_EXPORT if not f.endswith("README.md"))
    with pytest.raises(UploadError, match=r"missing: open-data/2026-11/README\.md"):
        plan_uploads(export_dir(tmp_path, files))


def test_plan_refuses_league_and_open_data_together(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="separately"):
        plan_uploads(export_dir(tmp_path, EXPORT + OPEN_DATA_EXPORT))


def test_no_hook_does_not_need_the_hook_setting() -> None:
    env = {k: v for k, v in ENV.items() if k != "PAGES_DEPLOY_HOOK_URL"}
    assert R2Settings.from_env(env, need_hook=False).bucket == ENV["R2_BUCKET"]
    with pytest.raises(UploadError, match="PAGES_DEPLOY_HOOK_URL"):
        R2Settings.from_env(env)


def test_a_reexport_with_fewer_parts_is_refused_not_mixed(tmp_path: Path) -> None:
    three_parts = export_dir(tmp_path / "first", OPEN_DATA_EXPORT)
    one_part = export_dir(
        tmp_path / "second",
        tuple(f for f in OPEN_DATA_EXPORT if "line_minute_status" not in f)
        + ("open-data/2026-11/line_minute_status.parquet",),
    )
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=ENV["R2_BUCKET"])
        first = plan_uploads(three_parts)
        assert upload.stale_open_data_keys(client, ENV["R2_BUCKET"], first) == []
        upload.upload(client, ENV["R2_BUCKET"], first)
        # The same files again replace themselves; nothing is stale.
        assert upload.stale_open_data_keys(client, ENV["R2_BUCKET"], first) == []
        assert upload.stale_open_data_keys(client, ENV["R2_BUCKET"], plan_uploads(one_part)) == [
            "open-data/2026-11/line_minute_status-000.parquet",
            "open-data/2026-11/line_minute_status-001.parquet",
        ]
        # A league upload never lists the bucket.
        assert (
            upload.stale_open_data_keys(
                client, ENV["R2_BUCKET"], plan_uploads(export_dir(tmp_path / "league"))
            )
            == []
        )


def test_settings_name_every_missing_secret() -> None:
    env = {**ENV, "R2_BUCKET": " ", "PAGES_DEPLOY_HOOK_URL": ""}
    with pytest.raises(UploadError) as info:
        R2Settings.from_env(env)
    assert str(info.value) == "missing settings: R2_BUCKET, PAGES_DEPLOY_HOOK_URL"


def test_settings_check_shapes_without_echoing_values() -> None:
    with pytest.raises(UploadError, match="R2_ACCOUNT_ID") as info:
        R2Settings.from_env({**ENV, "R2_ACCOUNT_ID": "not-an-id"})
    assert "not-an-id" not in str(info.value)
    with pytest.raises(UploadError, match="https") as info:
        R2Settings.from_env({**ENV, "PAGES_DEPLOY_HOOK_URL": "http://example.com/hook"})
    assert "example.com" not in str(info.value)
    with pytest.raises(UploadError, match="api.cloudflare.com") as info:
        R2Settings.from_env({**ENV, "PAGES_DEPLOY_HOOK_URL": "https://example.com/hook"})
    assert "example.com/hook" not in str(info.value)
    assert R2Settings.from_env(ENV).endpoint_url == ENDPOINT


class RecordingClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on

    def put_object(self, **kwargs: Any) -> None:
        if kwargs["Key"] == self.fail_on:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.calls.append(kwargs)


def test_upload_stops_at_the_first_failure(tmp_path: Path) -> None:
    planned = plan_uploads(export_dir(tmp_path))
    client = RecordingClient(fail_on="league/latest.json")
    with pytest.raises(UploadError, match=r"league/latest\.json failed: AccessDenied"):
        upload.upload(client, "b", planned)  # type: ignore[arg-type]
    sent = [c["Key"] for c in client.calls]
    assert "league/latest.json" not in sent
    assert "meta/index.json" not in sent
    assert len(sent) == len(EXPORT) - 2


@pytest.fixture
def r2(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    # moto answers for R2's endpoint as if it were S3.
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", ENDPOINT)
    with mock_aws():
        client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            region_name="us-east-1",
            aws_access_key_id="test-key-id",
            aws_secret_access_key="test-secret",
        )
        client.create_bucket(Bucket=ENV["R2_BUCKET"])
        yield client


def test_objects_land_with_their_headers(tmp_path: Path, r2: Any) -> None:
    planned = plan_uploads(export_dir(tmp_path))
    client = upload.r2_client(R2Settings.from_env(ENV))
    assert client.meta.endpoint_url == ENDPOINT
    upload.upload(client, ENV["R2_BUCKET"], planned)
    for obj in planned:
        head = r2.head_object(Bucket=ENV["R2_BUCKET"], Key=obj.key)
        assert head["ContentType"] == obj.content_type
        assert head["CacheControl"] == obj.cache_control
        body = r2.get_object(Bucket=ENV["R2_BUCKET"], Key=obj.key)["Body"].read()
        assert body == obj.path.read_bytes()


def hook_client(status: int, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"success": status < 400})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deploy_hook_is_a_bare_post() -> None:
    seen: list[httpx.Request] = []
    assert call_deploy_hook(HOOK, hook_client(200, seen)) == 200
    assert [(r.method, str(r.url)) for r in seen] == [("POST", HOOK)]


def test_deploy_hook_errors_never_carry_the_url() -> None:
    with pytest.raises(UploadError) as info:
        call_deploy_hook(HOOK, hook_client(403, []))
    assert "403" in str(info.value)
    assert "deploy_hooks" not in str(info.value)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(UploadError) as info:
        call_deploy_hook(HOOK, httpx.Client(transport=httpx.MockTransport(refuse)))
    assert "deploy_hooks" not in str(info.value)
    assert info.value.__cause__ is None


def test_main_uploads_then_calls_the_hook(
    tmp_path: Path, r2: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[httpx.Request] = []
    real_client = httpx.Client

    def fake_client(**kwargs: Any) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            # Every object is already in the bucket when the hook fires.
            listed = r2.list_objects_v2(Bucket=ENV["R2_BUCKET"])
            assert listed["KeyCount"] == len(EXPORT)
            seen.append(request)
            return httpx.Response(200)

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    assert upload.main(["--dir", str(export_dir(tmp_path))], env=ENV) == 0
    assert len(seen) == 1
    out = capsys.readouterr()
    for value in ENV.values():
        if value != ENV["R2_BUCKET"]:
            assert value not in out.out + out.err


def test_main_without_settings_fails_before_sending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert upload.main(["--dir", str(export_dir(tmp_path))], env={}) == 1
    assert "missing settings: R2_ACCOUNT_ID" in capsys.readouterr().err


def test_main_dry_run_needs_no_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert upload.main(["--dir", str(export_dir(tmp_path)), "--dry-run"], env={}) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(EXPORT)
    assert lines[-1].startswith("would upload meta/index.json")


def test_connection_errors_name_the_key_not_the_endpoint(tmp_path: Path) -> None:
    class Unreachable:
        def put_object(self, **kwargs: Any) -> None:
            raise EndpointConnectionError(endpoint_url=ENDPOINT)

    planned = plan_uploads(export_dir(tmp_path))
    with pytest.raises(UploadError) as info:
        upload.upload(Unreachable(), "b", planned)  # type: ignore[arg-type]
    assert "EndpointConnectionError" in str(info.value)
    assert ACCOUNT not in str(info.value)


def test_main_skips_the_hook_when_an_upload_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = RecordingClient(fail_on="lines/bakerloo.json")
    monkeypatch.setattr(upload, "r2_client", lambda settings: client)
    called: list[str] = []

    def hook(url: str, http: httpx.Client) -> int:
        called.append(url)
        return 200

    monkeypatch.setattr(upload, "call_deploy_hook", hook)
    assert upload.main(["--dir", str(export_dir(tmp_path))], env=ENV) == 1
    assert called == []
    assert "lines/bakerloo.json failed: AccessDenied" in capsys.readouterr().err
    assert "meta/index.json" not in [c["Key"] for c in client.calls]


def test_main_fails_when_the_hook_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = RecordingClient()
    monkeypatch.setattr(upload, "r2_client", lambda settings: client)
    real_client = httpx.Client

    def refusing(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kwargs)

    monkeypatch.setattr(httpx, "Client", refusing)
    assert upload.main(["--dir", str(export_dir(tmp_path))], env=ENV) == 1
    assert len(client.calls) == len(EXPORT)
    err = capsys.readouterr().err
    assert "HTTP 500" in err
    assert "deploy_hooks" not in err
