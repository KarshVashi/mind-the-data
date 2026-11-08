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


PARAMETER_READS = {"ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"}


def test_only_denies_are_parameter_reads() -> None:
    for s in statements():
        if s["Effect"] != "Allow":
            assert s["Effect"] == "Deny"
            assert set(as_list(s["Action"])) <= PARAMETER_READS, s


def test_project_parameters_are_denied() -> None:
    # ReadOnlyAccess allows ssm:Get*, and the AWS-managed SSM key decrypts for
    # any caller SSM authorises, so only an explicit deny keeps the TfL key out.
    denied: dict[str, set[str]] = {}
    for s in statements():
        if s["Effect"] == "Deny":
            for resource in as_list(s["Resource"]):
                denied.setdefault(resource, set()).update(as_list(s["Action"]))
    prefix = "arn:aws:ssm:eu-west-2:ACCOUNT_ID:parameter/mind-the-data"
    for resource in (prefix, prefix + "/*"):
        assert PARAMETER_READS <= denied.get(resource, set()), resource
    # A path read authorises against the path it names, so `--path /` would
    # otherwise return everything below it.
    assert "ssm:GetParametersByPath" in denied.get("*", set())


def test_allows_never_reach_parameters() -> None:
    for s in statements():
        if s["Effect"] == "Allow":
            assert not any(a.startswith("ssm:") for a in as_list(s["Action"])), s


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
