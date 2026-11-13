"""Upload the exported files to Cloudflare R2, then start the website's rebuild.

    uv run python -m mind_the_data.publish.upload --dir build/publish
    uv run python -m mind_the_data.publish.upload --dir build/publish --dry-run

The layout, content types, cache headers and upload order follow
docs/site-integration.md (section 2): week files, line files, cards and the
methodology first, then league/latest.json, then meta/index.json, so a
reader never sees an index that lists a file not yet uploaded. The Pages
deploy hook is called only after every upload succeeded.

Settings come from the environment, set from GitHub secrets in
publish-weekly.yml (runbook, H12): R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET and PAGES_DEPLOY_HOOK_URL. None of them is
ever printed.
"""

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

JSON = "application/json; charset=utf-8"
PNG = "image/png"

# Overwritten every Monday.
SHORT_CACHE = "public, max-age=300"
# Rewritten each run (D39) but changes only with the methodology or marts.
WEEK_CACHE = "public, max-age=3600"
# Rendered once for the week.
CARD_CACHE = "public, max-age=86400"

LATEST = "league/latest.json"
INDEX = "meta/index.json"
# Uploaded last, in this order.
FINAL_KEYS = (LATEST, INDEX)

_WEEK = r"\d{4}-W\d{2}"
# (pattern, content type, cache control). Anything else in the directory is
# refused rather than published.
RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"league/latest\.json"), JSON, SHORT_CACHE),
    (re.compile(rf"league/{_WEEK}\.json"), JSON, WEEK_CACHE),
    (re.compile(r"lines/[a-z-]+\.json"), JSON, SHORT_CACHE),
    (re.compile(r"meta/(index|methodology)\.json"), JSON, SHORT_CACHE),
    (re.compile(rf"cards/{_WEEK}(-1080x1350)?\.png"), PNG, CARD_CACHE),
)

SETTING_NAMES = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "PAGES_DEPLOY_HOOK_URL",
)


class UploadError(RuntimeError):
    """The directory, the settings or the deploy hook call are not usable."""


@dataclass(frozen=True)
class PlannedObject:
    key: str
    path: Path
    content_type: str
    cache_control: str


@dataclass(frozen=True)
class R2Settings:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    deploy_hook_url: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "R2Settings":
        missing = [name for name in SETTING_NAMES if not env.get(name, "").strip()]
        if missing:
            raise UploadError("missing settings: " + ", ".join(missing))
        settings = cls(*(env[name].strip() for name in SETTING_NAMES))
        if not re.fullmatch(r"[0-9a-f]{32}", settings.account_id):
            raise UploadError("R2_ACCOUNT_ID must be the 32-character Cloudflare account id")
        if urlsplit(settings.deploy_hook_url).scheme != "https":
            raise UploadError("PAGES_DEPLOY_HOOK_URL must be an https URL")
        return settings


def classify(key: str) -> tuple[str, str]:
    """Content type and Cache-Control for a published key; refuses unknown keys."""
    for pattern, content_type, cache_control in RULES:
        if pattern.fullmatch(key):
            return content_type, cache_control
    raise UploadError(f"not a published file: {key}")


def plan_uploads(directory: Path) -> list[PlannedObject]:
    """Every file under the directory as an object, in upload order."""
    if not directory.is_dir():
        raise UploadError(f"no such directory: {directory}")
    planned = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        key = path.relative_to(directory).as_posix()
        content_type, cache_control = classify(key)
        planned.append(PlannedObject(key, path, content_type, cache_control))
    keys = {p.key for p in planned}
    missing = [key for key in FINAL_KEYS if key not in keys]
    if missing:
        raise UploadError("export incomplete, missing: " + ", ".join(missing))
    rank = {key: i + 1 for i, key in enumerate(FINAL_KEYS)}
    # sorted() is stable, so everything else keeps its path order.
    return sorted(planned, key=lambda p: rank.get(p.key, 0))


def r2_client(settings: R2Settings) -> "S3Client":
    """An S3 client for R2's S3-compatible API.

    Checksums are sent only where the API requires them: R2 does not accept
    every checksum header newer SDKs add by default. The explicit keys also
    keep the job's AWS credentials (for Athena) away from R2.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name="auto",
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def upload(client: "S3Client", bucket: str, planned: Sequence[PlannedObject]) -> list[str]:
    """Put each object in order; stops at the first failure."""
    done = []
    for obj in planned:
        client.put_object(
            Bucket=bucket,
            Key=obj.key,
            Body=obj.path.read_bytes(),
            ContentType=obj.content_type,
            CacheControl=obj.cache_control,
        )
        done.append(obj.key)
    return done


def call_deploy_hook(url: str, client: httpx.Client) -> int:
    """POST to the Pages deploy hook; returns the status code.

    Errors never carry the URL: anyone holding it can start builds.
    """
    try:
        response = client.post(url)
    except httpx.HTTPError as exc:
        raise UploadError(f"deploy hook call failed: {type(exc).__name__}") from None
    if not response.is_success:
        raise UploadError(f"deploy hook returned HTTP {response.status_code}")
    return response.status_code


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the export's --out-dir")
    parser.add_argument("--dry-run", action="store_true", help="list the uploads; send nothing")
    parser.add_argument("--no-hook", action="store_true", help="upload without the rebuild")
    args = parser.parse_args(argv)

    try:
        planned = plan_uploads(args.dir)
        if args.dry_run:
            for obj in planned:
                print(f"would upload {obj.key} ({obj.content_type}; {obj.cache_control})")
            return 0
        settings = R2Settings.from_env(os.environ if env is None else env)
        keys = upload(r2_client(settings), settings.bucket, planned)
        print(f"uploaded {len(keys)} objects, {', '.join(FINAL_KEYS)} last")
        if args.no_hook:
            print("deploy hook not called (--no-hook)")
            return 0
        with httpx.Client(timeout=30) as http:
            status = call_deploy_hook(settings.deploy_hook_url, http)
        print(f"deploy hook accepted (HTTP {status})")
    except UploadError as exc:
        print(f"upload: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
