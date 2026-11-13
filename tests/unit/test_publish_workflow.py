"""publish-weekly.yml against the upload code and the London clock."""

import re
from pathlib import Path
from typing import Any

import yaml

from mind_the_data.publish.export import PUBLISH_WORKGROUP
from mind_the_data.publish.upload import SETTING_NAMES

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish-weekly.yml"


def load() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return data


def steps(job: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load()["jobs"][job]["steps"]
    return result


def test_both_cron_slots_are_kept_by_the_gate_for_one_offset_each() -> None:
    # PyYAML reads the "on" key as True.
    crons = [entry["cron"] for entry in load()[True]["schedule"]]
    assert crons == ["30 5 * * 1", "30 6 * * 1"]
    clock = next(step for step in steps("gate") if step.get("id") == "clock")["run"]
    assert '"30 5 * * 1|+0100"' in clock  # 06:30 BST
    assert '"30 6 * * 1|+0000"' in clock  # 06:30 GMT


def test_manual_runs_default_to_dry_run() -> None:
    inputs = load()[True]["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["type"] == "boolean"
    assert inputs["dry_run"]["default"] is True


def test_upload_step_gets_exactly_the_settings_the_code_reads() -> None:
    upload = next(step for step in steps("publish") if step.get("name", "").startswith("upload"))
    assert upload["if"] == "env.DRY_RUN != 'true'"
    assert tuple(upload["env"]) == SETTING_NAMES
    for name, value in upload["env"].items():
        assert value == f"${{{{ secrets.{name} }}}}"
    assert "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN" in upload["run"]


def test_secrets_reach_only_the_presence_check_and_the_upload() -> None:
    text = WORKFLOW.read_text()
    assert set(re.findall(r"secrets\.([A-Z0-9_]+)", text)) == set(SETTING_NAMES)
    users = [
        step.get("id") or step.get("name")
        for job in ("gate", "publish")
        for step in steps(job)
        if "secrets." in str(step)
    ]
    assert users == ["config", "upload to R2 and rebuild the site"]


def test_export_runs_through_the_publish_workgroup() -> None:
    export = next(step for step in steps("publish") if step.get("name") == "export")
    assert "--publish-workgroup" in export["run"]
    assert PUBLISH_WORKGROUP == "mind-the-data-prod"
    assert "mtd-ci-publish" in load()["env"]["PUBLISH_ROLE_ARN"]


def test_downloads_come_before_aws_credentials() -> None:
    names = [step.get("name") or step.get("uses", "") for step in steps("publish")]
    creds = next(i for i, n in enumerate(names) if "configure-aws-credentials" in n)
    assert names.index("install") < creds
    assert names.index("install chromium") < creds
