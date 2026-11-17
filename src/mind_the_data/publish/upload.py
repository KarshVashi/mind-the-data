"""Upload the exported files to Cloudflare R2, then start the website's rebuild.

    uv run python -m mind_the_data.publish.upload --dir build/publish
    uv run python -m mind_the_data.publish.upload --dir build/publish --dry-run

The layout, content types, cache headers and upload order follow
docs/site-integration.md (section 2): week, line, division and season files,
cards and the methodology first, then league/latest.json, then meta/index.json, so a
reader never sees an index that lists a file not yet uploaded. The Pages
deploy hook is called only after every upload succeeded.

Settings come from the environment, set from GitHub secrets in
publish-weekly.yml (runbook, H12): R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET and PAGES_DEPLOY_HOOK_URL. None of them is
ever printed, including in errors.

The monthly open dataset (open-data/{yyyy-mm}/, mind_the_data.publish.open_data)
is uploaded the same way, as a directory of its own: the Parquet files and
README.md first, manifest.json last. The two kinds are never mixed in one run.
A re-export whose files would leave an older part of the month in place is
refused (stale_open_data_keys), since this code never deletes.

Nothing is ever deleted from the bucket: a key that drops out of
meta/index.json (an old card, a line no longer listed) stays readable at its
old URL. The site reads only what the index lists, so this is deliberate.
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
PARQUET = "application/vnd.apache.parquet"
MARKDOWN = "text/markdown; charset=utf-8"

# Overwritten each weekly run.
SHORT_CACHE = "public, max-age=300"
# Rewritten each run (D39) but changes only with the methodology or marts.
WEEK_CACHE = "public, max-age=3600"
# Rendered once for the week.
CARD_CACHE = "public, max-age=86400"
# Written once a month; a re-export after a fix replaces them.
OPEN_DATA_CACHE = "public, max-age=3600"

LATEST = "league/latest.json"
INDEX = "meta/index.json"
# Uploaded last, in this order.
FINAL_KEYS = (LATEST, INDEX)

_WEEK = r"\d{4}-W\d{2}"
_MONTH = r"\d{4}-(?:0[1-9]|1[0-2])"
OPEN_DATA = "open-data/"
# The datasets mind_the_data.publish.open_data writes (a test keeps the two in step).
OPEN_DATASETS = ("line_minute_status", "line_daily", "line_weekly")
# Each month's manifest lists its files, so it goes up after them.
OPEN_DATA_MANIFEST = re.compile(rf"open-data/{_MONTH}/manifest\.json")
# (pattern, content type, cache control). Anything else in the directory is
# refused rather than published.
RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"league/latest\.json"), JSON, SHORT_CACHE),
    (re.compile(rf"league/{_WEEK}\.json"), JSON, WEEK_CACHE),
    (re.compile(r"lines/[a-z-]+\.json"), JSON, SHORT_CACHE),
    (re.compile(r"meta/(index|methodology)\.json"), JSON, SHORT_CACHE),
    # Rewritten every run like the line files. The pattern also matches stations/index.json.
    (re.compile(r"stations/[0-9A-Za-z]+\.json"), JSON, SHORT_CACHE),
    # Rewritten each run while the month or season is still open (D57, D58).
    (re.compile(rf"divisions/{_MONTH}\.json"), JSON, SHORT_CACHE),
    (re.compile(r"season/\d{4}-\d{2}\.json"), JSON, SHORT_CACHE),
    (re.compile(rf"cards/{_WEEK}(-1080x1350)?\.png"), PNG, CARD_CACHE),
    (
        re.compile(rf"open-data/{_MONTH}/({'|'.join(OPEN_DATASETS)})(-\d{{3}})?\.parquet"),
        PARQUET,
        OPEN_DATA_CACHE,
    ),
    (re.compile(rf"open-data/{_MONTH}/README\.md"), MARKDOWN, OPEN_DATA_CACHE),
    (OPEN_DATA_MANIFEST, JSON, OPEN_DATA_CACHE),
)

# Pages deploy hooks are all served from Cloudflare's API host; anything else
# is a mis-pasted secret.
DEPLOY_HOOK_HOST = "api.cloudflare.com"

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
    def from_env(cls, env: Mapping[str, str], *, need_hook: bool = True) -> "R2Settings":
        """Settings from the environment; the deploy hook only when it will be called."""
        names = SETTING_NAMES if need_hook else SETTING_NAMES[:-1]
        missing = [name for name in names if not env.get(name, "").strip()]
        if missing:
            raise UploadError("missing settings: " + ", ".join(missing))
        settings = cls(*(env.get(name, "").strip() for name in SETTING_NAMES))
        if not re.fullmatch(r"[0-9a-f]{32}", settings.account_id):
            raise UploadError("R2_ACCOUNT_ID must be the 32-character Cloudflare account id")
        if need_hook:
            hook = urlsplit(settings.deploy_hook_url)
            if hook.scheme != "https" or hook.hostname != DEPLOY_HOOK_HOST:
                raise UploadError(
                    f"PAGES_DEPLOY_HOOK_URL must be an https URL on {DEPLOY_HOOK_HOST}"
                )
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
    open_data = {key for key in keys if key.startswith(OPEN_DATA)}
    if open_data and open_data != keys:
        raise UploadError("upload the league files and the open dataset separately")
    if open_data:
        months = sorted({k.split("/")[1] for k in keys})
        final_keys = tuple(f"open-data/{m}/manifest.json" for m in months)
        required = [*(f"open-data/{m}/README.md" for m in months), *final_keys]
    else:
        final_keys = FINAL_KEYS
        required = list(FINAL_KEYS)
    missing = [key for key in required if key not in keys]
    if missing:
        raise UploadError("export incomplete, missing: " + ", ".join(missing))
    rank = {key: i + 1 for i, key in enumerate(final_keys)}
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
    """Put each object in order; stops at the first failure.

    SDK errors become UploadError naming the key and the error class or
    code only, since botocore's messages can carry the account endpoint.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    done = []
    for obj in planned:
        try:
            client.put_object(
                Bucket=bucket,
                Key=obj.key,
                Body=obj.path.read_bytes(),
                ContentType=obj.content_type,
                CacheControl=obj.cache_control,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "unknown")
            raise UploadError(f"upload of {obj.key} failed: {code}") from None
        except BotoCoreError as exc:
            raise UploadError(f"upload of {obj.key} failed: {type(exc).__name__}") from None
        done.append(obj.key)
    return done


def stale_open_data_keys(
    client: "S3Client", bucket: str, planned: Sequence[PlannedObject]
) -> list[str]:
    """Keys already under a planned open-data month that this upload would not overwrite.

    Athena may split a dataset into a different number of parts on a
    re-export. Old parts left beside the new ones would be read together
    by anyone globbing the month, and the upload never deletes, so it
    refuses instead. Empty for a league upload.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    planned_keys = {p.key for p in planned}
    months = sorted({k.split("/")[1] for k in planned_keys if k.startswith(OPEN_DATA)})
    stale: list[str] = []
    for month in months:
        try:
            pages = client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=f"{OPEN_DATA}{month}/"
            )
            existing = [obj["Key"] for page in pages for obj in page.get("Contents", [])]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "unknown")
            raise UploadError(f"listing open-data/{month}/ failed: {code}") from None
        except BotoCoreError as exc:
            raise UploadError(f"listing open-data/{month}/ failed: {type(exc).__name__}") from None
        stale += sorted(k for k in existing if k not in planned_keys)
    return stale


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
        settings = R2Settings.from_env(
            os.environ if env is None else env, need_hook=not args.no_hook
        )
        client = r2_client(settings)
        stale = stale_open_data_keys(client, settings.bucket, planned)
        if stale:
            raise UploadError(
                "the bucket holds open-data files this export does not replace, which "
                "readers globbing the month would mix in: " + ", ".join(stale) + ". Remove "
                "them in the Cloudflare dashboard, then re-run (runbook, Monthly open data)"
            )
        keys = upload(client, settings.bucket, planned)
        print(f"uploaded {len(keys)} objects, {keys[-1]} last")
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
