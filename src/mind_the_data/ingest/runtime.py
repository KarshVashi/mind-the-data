"""Lambda and local wiring: settings from the environment, AWS clients, writers."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from mind_the_data.common.config import Line, Station, default_config_dir, load_config
from mind_the_data.common.writer import LocalRawWriter, RawWriter, S3RawWriter
from mind_the_data.ingest.app_key import KeySource, resolve_app_key


@dataclass(frozen=True)
class Settings:
    raw_bucket: str | None
    metric_namespace: str
    poll_budget_s: float
    config_dir: Path
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        return cls(
            raw_bucket=env.get("RAW_BUCKET") or None,
            metric_namespace=env.get("METRIC_NAMESPACE", "MindTheData"),
            poll_budget_s=float(env.get("POLL_BUDGET_SECONDS", "45")),
            config_dir=Path(env["CONFIG_DIR"]) if env.get("CONFIG_DIR") else default_config_dir(),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )


@cache
def app_key() -> tuple[str | None, KeySource]:
    """Resolved once per Lambda container; never logged."""
    import boto3

    return resolve_app_key(os.environ, lambda: boto3.client("ssm"))


@cache
def config(config_dir: Path) -> tuple[list[Line], list[Station]]:
    return load_config(config_dir)


def raw_writer(settings: Settings, local_out: Path | None = None) -> RawWriter:
    if local_out is not None:
        return LocalRawWriter(local_out)
    if not settings.raw_bucket:
        raise RuntimeError("RAW_BUCKET is not set")
    import boto3

    return S3RawWriter(boto3.client("s3"), settings.raw_bucket)
