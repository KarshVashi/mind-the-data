"""Resolve the TfL app key: SSM in Lambda, an env var locally, else anonymous.

The key is never logged and never stored in raw data; only where it came
from is reported.
"""

import logging
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

logger = logging.getLogger(__name__)


class KeySource(StrEnum):
    SSM = "ssm"
    ENV = "env"
    ANONYMOUS = "anonymous"


class _SsmClient(Protocol):
    def get_parameter(self, *, Name: str, WithDecryption: bool) -> Mapping[str, object]: ...


def resolve_app_key(
    env: Mapping[str, str], ssm_factory: Callable[[], _SsmClient]
) -> tuple[str | None, KeySource]:
    parameter = env.get("TFL_APP_KEY_PARAMETER", "").strip()
    if parameter:
        response = ssm_factory().get_parameter(Name=parameter, WithDecryption=True)
        value = response.get("Parameter")
        key = value.get("Value") if isinstance(value, Mapping) else None
        if not isinstance(key, str) or not key.strip():
            # An empty key would silently fall back to the anonymous limit.
            raise RuntimeError(f"SSM parameter {parameter} has no usable value")
        return key.strip(), KeySource.SSM

    local = env.get("TFL_APP_KEY", "").strip()
    if local:
        return local, KeySource.ENV

    logger.warning("no TfL app key configured; using the anonymous rate limit")
    return None, KeySource.ANONYMOUS
