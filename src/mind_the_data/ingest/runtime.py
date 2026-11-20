"""Lambda and local wiring: settings from the environment, AWS clients, writers."""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from mind_the_data.common.clock import ensure_utc
from mind_the_data.common.config import (
    CrowdingStation,
    Line,
    Station,
    default_config_dir,
    load_config,
    load_crowding_stations,
)
from mind_the_data.common.telemetry import configure_logging
from mind_the_data.common.writer import LocalRawWriter, RawWriter, S3RawWriter
from mind_the_data.ingest.app_key import KeySource, resolve_app_key

logger = logging.getLogger(__name__)

# Time kept back from the Lambda deadline for writing raw objects and logging.
WRITE_MARGIN_S = 10.0
MAX_BUDGET_S = 600.0


@dataclass(frozen=True)
class Settings:
    raw_bucket: str | None
    metric_namespace: str
    budget_s: float
    max_concurrency: int
    config_dir: Path
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        budget = float(env.get("POLL_BUDGET_SECONDS", "45"))
        if not 0 < budget <= MAX_BUDGET_S:
            raise ValueError(f"POLL_BUDGET_SECONDS must be in (0, {MAX_BUDGET_S:g}], got {budget}")
        concurrency = int(env.get("MAX_CONCURRENCY", "10"))
        if not 1 <= concurrency <= 20:
            raise ValueError(f"MAX_CONCURRENCY must be in [1, 20], got {concurrency}")
        return cls(
            raw_bucket=env.get("RAW_BUCKET") or None,
            metric_namespace=env.get("METRIC_NAMESPACE", "MindTheData"),
            budget_s=budget,
            max_concurrency=concurrency,
            config_dir=Path(env["CONFIG_DIR"]) if env.get("CONFIG_DIR") else default_config_dir(),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )


def scheduled_slot(event: Mapping[str, Any], fallback: datetime) -> datetime:
    """The schedule's own time when present, so a late run keeps its minute's cadence."""
    raw = event.get("scheduled_time")
    if isinstance(raw, str):
        try:
            return ensure_utc(datetime.fromisoformat(raw))
        except ValueError:
            logger.warning("unparseable scheduled_time; using the clock")
    return fallback


def budget_for(settings: Settings, context: object | None) -> float:
    """The configured budget, capped by what the Lambda has left minus a write margin."""
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if remaining_ms is None:
        return settings.budget_s
    available = float(remaining_ms()) / 1000 - WRITE_MARGIN_S
    return max(1.0, min(settings.budget_s, available))


@cache
def cold_start(log_level: str) -> None:
    """Logging is configured once per container, not on every invocation."""
    configure_logging(log_level)


@cache
def app_key() -> tuple[str | None, KeySource]:
    """Resolved once per container and cleared after an auth failure; never logged."""
    import boto3

    key, source = resolve_app_key(os.environ, lambda: boto3.client("ssm"))
    logger.info("app key resolved", extra={"key_source": source.value})
    return key, source


@cache
def config(config_dir: Path) -> tuple[list[Line], list[Station]]:
    return load_config(config_dir)


@cache
def crowding_stations(config_dir: Path) -> list[CrowdingStation]:
    return load_crowding_stations(config_dir / "crowding_stations.yaml")


def raw_writer(settings: Settings, local_out: Path | None = None) -> RawWriter:
    if local_out is not None:
        return LocalRawWriter(local_out)
    if not settings.raw_bucket:
        raise RuntimeError("RAW_BUCKET is not set")
    import boto3

    return S3RawWriter(boto3.client("s3"), settings.raw_bucket)
