"""transform-daily.yml: the Sunday maintenance gate and the guarded full refresh."""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "transform-daily.yml"


def load() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return data


def step(job: str, key: str, value: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = load()["jobs"][job]["steps"]
    return next(s for s in steps if s.get(key) == value)


def run_step(step_id: str, env: dict[str, str], tmp_path: Path) -> tuple[int, dict[str, str], str]:
    script = step("gate", "id", step_id)["run"]
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


def test_full_refresh_is_a_manual_input_only() -> None:
    inputs = load()[True]["workflow_dispatch"]["inputs"]
    assert inputs["full_refresh"] == {
        "description": inputs["full_refresh"]["description"],
        "type": "boolean",
        "default": False,
    }
    assert inputs["select"]["default"] == ""
    assert inputs["maintenance"]["default"] is False


def test_maintenance_runs_on_the_london_sunday(tmp_path: Path) -> None:
    code, out, _ = run_step("clock", {"EVENT": "schedule", "SCHEDULE": "30 3 * * *"}, tmp_path)
    assert code == 0
    sunday = datetime.now(ZoneInfo("Europe/London")).isoweekday() == 7
    assert out["maintenance"] == str(sunday).lower()


def test_maintenance_on_request(tmp_path: Path) -> None:
    env = {"EVENT": "workflow_dispatch", "MAINTENANCE_INPUT": "true"}
    code, out, _ = run_step("clock", env, tmp_path)
    assert (code, out["run"], out["maintenance"]) == (0, "true", "true")


@pytest.mark.parametrize(
    ("env", "code", "full_refresh", "select"),
    [
        # A schedule has no inputs; even if one leaked through it is ignored.
        ({"EVENT": "schedule", "FULL_REFRESH_INPUT": "true", "SELECT_INPUT": "x"}, 0, "false", ""),
        ({"EVENT": "workflow_dispatch"}, 0, "false", ""),
        (
            {"EVENT": "workflow_dispatch", "SELECT_INPUT": "fct_line_weekly+"},
            0,
            "false",
            "fct_line_weekly+",
        ),
        (
            {
                "EVENT": "workflow_dispatch",
                "FULL_REFRESH_INPUT": "true",
                "SELECT_INPUT": "stg_arrivals+",
            },
            0,
            "true",
            "stg_arrivals+",
        ),
        (
            {
                "EVENT": "workflow_dispatch",
                "FULL_REFRESH_INPUT": "true",
                "SELECT_INPUT": "path:models",
            },
            0,
            "true",
            "path:models",
        ),
    ],
)
def test_options(
    env: dict[str, str], code: int, full_refresh: str, select: str, tmp_path: Path
) -> None:
    rc, out, log = run_step("options", env, tmp_path)
    assert rc == code, log
    assert out == {"full_refresh": full_refresh, "select": select}


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FULL_REFRESH_INPUT": "true"}, "Selector needed"),
        ({"FULL_REFRESH_INPUT": "true", "SELECT_INPUT": "   "}, "Selector needed"),
        ({"SELECT_INPUT": "stg_arrivals; rm -rf /"}, "Bad selector"),
        ({"SELECT_INPUT": "$(id)"}, "Bad selector"),
        ({"SELECT_INPUT": "stg_x --target dev"}, "options"),
        ({"SELECT_INPUT": "stg_x --full-refresh"}, "options"),
        ({"SELECT_INPUT": "-s stg_x"}, "options"),
        ({"FULL_REFRESH_INPUT": "true", "SELECT_INPUT": "x --exclude y"}, "options"),
        ({"SELECT_INPUT": "a\nb"}, "Bad selector"),
    ],
)
def test_options_refuse(env: dict[str, str], message: str, tmp_path: Path) -> None:
    rc, _, log = run_step("options", {"EVENT": "workflow_dispatch", **env}, tmp_path)
    assert rc == 1
    assert message in log


def test_build_reads_the_gate_outputs_not_the_inputs() -> None:
    build = step("build", "name", "dbt build")
    assert build["env"] == {
        "FULL_REFRESH": "${{ needs.gate.outputs.full_refresh }}",
        "SELECT": "${{ needs.gate.outputs.select }}",
    }
    assert "inputs." not in str(load()["jobs"]["build"])


def test_maintenance_follows_the_build_as_the_transform_role() -> None:
    names = [s.get("name") or s.get("id") for s in load()["jobs"]["build"]["steps"]]
    assert names.index("creds") < names.index("dbt build") < names.index("iceberg maintenance")
    maintenance = step("build", "name", "iceberg maintenance")
    assert "needs.gate.outputs.maintenance == 'true'" in maintenance["if"]
    assert "steps.creds.outcome == 'success'" in maintenance["if"]
    assert maintenance["run"].endswith(
        "dbt run-operation iceberg_maintenance --target prod --profiles-dir ."
    )


def test_stale_tmp_tables_are_dropped_before_the_build() -> None:
    names = [s.get("name") or s.get("id") for s in load()["jobs"]["build"]["steps"]]
    assert names.index("creds") < names.index("drop stale tmp tables") < names.index("dbt build")
    cleanup = step("build", "name", "drop stale tmp tables")
    # A failed cleanup must never stop the build from running.
    assert cleanup["continue-on-error"] is True
    assert cleanup["run"].endswith(
        "dbt run-operation drop_stale_tmp_relations --target prod --profiles-dir ."
    )


def test_station_models_build_in_their_own_step() -> None:
    build = step("build", "name", "dbt build")
    assert "--exclude tag:station" in build["run"]
    names = [s.get("name") or s.get("id") for s in load()["jobs"]["build"]["steps"]]
    assert names.index("dbt build") < names.index("dbt build station models")
    station = step("build", "name", "dbt build station models")
    # A station failure must never fail the line-level job.
    assert station["continue-on-error"] is True
    assert "--select tag:station" in station["run"]
    assert "needs.gate.outputs.select == ''" in station["if"]


def test_exploratory_models_stay_out_of_the_daily_build() -> None:
    build = step("build", "name", "dbt build")
    assert "--exclude tag:station tag:exploratory" in build["run"]
    station = step("build", "name", "dbt build station models")
    assert "exploratory" not in station["run"]
