"""The H11 inline policy for the dev identity must never reach prod or raw writes."""

import json
from pathlib import Path
from typing import Any

POLICY = Path(__file__).resolve().parents[2] / "docs" / "policies" / "mtd-dev-athena.json"
WRITE_PREFIXES = (
    "s3:Put",
    "s3:Delete",
    "s3:Abort",
    "glue:Create",
    "glue:Update",
    "glue:Delete",
    "glue:Batch",
)


def statements() -> list[dict[str, Any]]:
    doc = json.loads(POLICY.read_text())
    assert doc["Version"] == "2012-10-17"
    return list(doc["Statement"])


def as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def test_only_allows() -> None:
    assert all(s["Effect"] == "Allow" for s in statements())


def test_nothing_names_prod() -> None:
    assert "prod" not in POLICY.read_text()


def test_writes_reach_dev_only() -> None:
    for s in statements():
        if any(a.startswith(WRITE_PREFIXES) for a in as_list(s["Action"])):
            for resource in as_list(s["Resource"]):
                assert resource.endswith((":catalog", "_dev", "_dev/*", "/dev/*")), resource


def test_no_wildcard_actions() -> None:
    for s in statements():
        assert not any("*" in a for a in as_list(s["Action"]))
