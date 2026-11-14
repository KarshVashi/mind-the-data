"""publish-weekly.yml against the upload code and the London clock."""

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from mind_the_data.publish.export import PUBLISH_WORKGROUP
from mind_the_data.publish.upload import SETTING_NAMES

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish-weekly.yml"
ALL_SECRETS = {f"HAVE_{name}": "true" for name in SETTING_NAMES}
BASE = {
    "PUBLISH_ROLE_ARN": "arn:aws:iam::123456789012:role/mtd-ci-publish",
    "REF": "refs/heads/main",
}


def load() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return data


def steps(job: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load()["jobs"][job]["steps"]
    return result


def test_both_cron_slots_are_kept_by_the_gate_for_one_offset_each() -> None:
    # PyYAML reads the "on" key as True.
    crons = [entry["cron"] for entry in load()[True]["schedule"]]
    assert crons == ["30 5 * * 1", "30 6 * * 1", "45 5 2 * *", "45 6 2 * *"]
    clock = next(step for step in steps("gate") if step.get("id") == "clock")["run"]
    assert '"30 5 * * 1|+0100"' in clock  # 06:30 BST
    assert '"30 6 * * 1|+0000"' in clock  # 06:30 GMT
    assert '"45 5 2 * *|+0100"' in clock  # open data, 06:45 BST
    assert '"45 6 2 * *|+0000"' in clock  # open data, 06:45 GMT


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
        for job in load()["jobs"]
        for step in steps(job)
        if "secrets." in str(step)
    ]
    assert users == ["config", "upload to R2 and rebuild the site", "upload open data to R2"]


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


def test_only_presence_of_secrets_reaches_the_gate() -> None:
    config = next(step for step in steps("gate") if step.get("id") == "config")
    for name in SETTING_NAMES:
        assert config["env"][f"HAVE_{name}"] == f"${{{{ secrets.{name} != '' }}}}"
        assert name not in config["env"]


def run_step(step_id: str, env: dict[str, str], tmp_path: Path) -> tuple[int, dict[str, str], str]:
    """Run a gate step's shell as Actions would, returning its outputs."""
    script = next(step for step in steps("gate") if step.get("id") == step_id)["run"]
    output = tmp_path / f"{step_id}.out"
    output.write_text("")
    done = subprocess.run(
        ["bash", "-e", "-c", script],
        env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    pairs = dict(line.split("=", 1) for line in output.read_text().splitlines() if line)
    return done.returncode, pairs, done.stdout + done.stderr


def london_offset() -> str:
    return datetime.now(ZoneInfo("Europe/London")).strftime("%z")


@pytest.mark.parametrize("slot", ["30 5 * * 1", "30 6 * * 1"])
def test_clock_keeps_the_slot_that_is_0630_in_london(slot: str, tmp_path: Path) -> None:
    code, out, _ = run_step("clock", {"EVENT": "schedule", "SCHEDULE": slot}, tmp_path)
    assert code == 0
    expected = {"+0100": "30 5 * * 1", "+0000": "30 6 * * 1"}[london_offset()]
    assert out == {"run": str(slot == expected).lower(), "export": "league", "dry_run": "false"}


@pytest.mark.parametrize("slot", ["45 5 2 * *", "45 6 2 * *"])
def test_monthly_slot_exports_open_data(slot: str, tmp_path: Path) -> None:
    code, out, _ = run_step("clock", {"EVENT": "schedule", "SCHEDULE": slot}, tmp_path)
    assert code == 0
    expected = {"+0100": "45 5 2 * *", "+0000": "45 6 2 * *"}[london_offset()]
    assert out["run"] == str(slot == expected).lower()
    if out["run"] == "true":
        assert out["export"] == "open-data"


def previous_london_month() -> str:
    first = datetime.now(ZoneInfo("Europe/London")).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


@pytest.mark.parametrize(
    ("env", "ready", "notice"),
    [
        # Scheduled open data waits for D63 (OPEN_DATA_ENABLED), not PUBLISH_ENABLED.
        (
            {"EVENT": "schedule", "DRY_RUN": "false", "PUBLISH_ENABLED": "true", **ALL_SECRETS},
            "false",
            "OPEN_DATA_ENABLED",
        ),
        (
            {"EVENT": "schedule", "DRY_RUN": "false", "OPEN_DATA_ENABLED": "true", **ALL_SECRETS},
            "true",
            None,
        ),
    ],
)
def test_config_for_scheduled_open_data(
    env: dict[str, str], ready: str, notice: str | None, tmp_path: Path
) -> None:
    code, out, log = run_step("config", {**BASE, "EXPORT": "open-data", **env}, tmp_path)
    assert code == 0, log
    assert (out["ready"], out["month"]) == (ready, previous_london_month())
    if notice:
        assert notice in log


def test_config_checks_the_month_input(tmp_path: Path) -> None:
    env = {**BASE, "EVENT": "workflow_dispatch", "DRY_RUN": "true", "EXPORT": "open-data"}
    code, out, _ = run_step("config", {**env, "MONTH_INPUT": "2026-11"}, tmp_path)
    assert (code, out["month"]) == (0, "2026-11")
    code, _, log = run_step("config", {**env, "MONTH_INPUT": "2026-13"}, tmp_path)
    assert code == 1
    assert "Bad month" in log


def test_open_data_job_uploads_without_the_deploy_hook() -> None:
    upload = next(s for s in steps("open-data") if s.get("name") == "upload open data to R2")
    assert upload["if"] == "env.DRY_RUN != 'true'"
    assert tuple(upload["env"]) == SETTING_NAMES
    assert upload["run"].rstrip().endswith("--no-hook")
    export = next(s for s in steps("open-data") if s.get("name") == "export")
    assert "--publish-workgroup" in export["run"]
    jobs = load()["jobs"]
    assert "export == 'league'" in jobs["publish"]["if"]
    assert "export == 'open-data'" in jobs["open-data"]["if"]


@pytest.mark.parametrize(
    ("dry_run_input", "expected"), [("true", "true"), ("", "true"), ("false", "false")]
)
def test_manual_runs_are_dry_unless_asked(
    dry_run_input: str, expected: str, tmp_path: Path
) -> None:
    env = {"EVENT": "workflow_dispatch", "DRY_RUN_INPUT": dry_run_input}
    code, out, _ = run_step("clock", env, tmp_path)
    assert (code, out) == (0, {"run": "true", "export": "league", "dry_run": expected})


def test_manual_runs_can_pick_open_data(tmp_path: Path) -> None:
    env = {"EVENT": "workflow_dispatch", "EXPORT_INPUT": "open-data"}
    code, out, _ = run_step("clock", env, tmp_path)
    assert (code, out["export"], out["dry_run"]) == (0, "open-data", "true")


def previous_london_week() -> str:
    year, week, _ = (datetime.now(ZoneInfo("Europe/London")) - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


@pytest.mark.parametrize(
    ("env", "ready", "notice"),
    [
        # Before H12: nothing to publish to.
        ({"EVENT": "schedule", "DRY_RUN": "false"}, "false", "Blocked on H12"),
        # Secrets set, but the owner has not switched the schedule on (D36).
        ({"EVENT": "schedule", "DRY_RUN": "false", **ALL_SECRETS}, "false", "PUBLISH_ENABLED"),
        (
            {"EVENT": "schedule", "DRY_RUN": "false", "PUBLISH_ENABLED": "true", **ALL_SECRETS},
            "true",
            None,
        ),
        # A manual real run needs the secrets but not the variable.
        ({"EVENT": "workflow_dispatch", "DRY_RUN": "false", **ALL_SECRETS}, "true", None),
        (
            {
                "EVENT": "workflow_dispatch",
                "DRY_RUN": "false",
                **ALL_SECRETS,
                "HAVE_R2_BUCKET": "false",
            },
            "false",
            "not set: R2_BUCKET.",
        ),
        # A dry run needs neither.
        ({"EVENT": "workflow_dispatch", "DRY_RUN": "true"}, "true", None),
        # No publish role configured yet.
        (
            {"EVENT": "workflow_dispatch", "DRY_RUN": "true", "PUBLISH_ROLE_ARN": ""},
            "false",
            "AWS_PUBLISH_ROLE_ARN",
        ),
    ],
)
def test_config_decides_whether_to_publish(
    env: dict[str, str], ready: str, notice: str | None, tmp_path: Path
) -> None:
    code, out, log = run_step("config", {**BASE, **env}, tmp_path)
    assert code == 0, log
    assert out["ready"] == ready
    if notice:
        assert notice in log
    else:
        assert "::notice" not in log
    if env["EVENT"] == "schedule":
        assert out["week"] == previous_london_week()
    else:
        assert out["week"] == ""


def test_config_passes_a_valid_week_and_refuses_others(tmp_path: Path) -> None:
    env = {**BASE, "EVENT": "workflow_dispatch", "DRY_RUN": "true"}
    code, out, _ = run_step("config", {**env, "WEEK_INPUT": "2026-W46"}, tmp_path)
    assert (code, out["week"]) == (0, "2026-W46")
    code, _, log = run_step("config", {**env, "WEEK_INPUT": "2026-W46; echo hi"}, tmp_path)
    assert code == 1
    assert "Bad week" in log


def test_config_refuses_a_branch_the_role_does_not_trust(tmp_path: Path) -> None:
    env = {**BASE, "EVENT": "workflow_dispatch", "DRY_RUN": "true", "REF": "refs/heads/feat/x"}
    code, _, log = run_step("config", env, tmp_path)
    assert code == 1
    assert "publish needs main" in log
