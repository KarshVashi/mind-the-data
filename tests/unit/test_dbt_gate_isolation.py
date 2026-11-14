"""A failing measured-view gate never skips the declared tables (ADR-014).

`dbt build` makes a test block every node that descends from all the nodes
the test reads (dbt's test edges). The quality gate test reads
int_measured_hours, so the declared models must not descend from it, or
from anything else on the measured path. This reads the refs from the SQL
files, so it needs no dbt parse.
"""

import re
from pathlib import Path

TRANSFORM = Path(__file__).resolve().parents[2] / "transform"
REF = re.compile(r"""ref\(\s*['"](\w+)['"]\s*\)""")
GATE_TEST = "assert_measured_hours_pass_the_gate"

# The measured path: arrivals inference and everything gated by it.
MEASURED_ROOTS = (
    "int_inferred_arrivals",
    "int_headways",
    "int_vehicle_passes",
    "int_measured_hours",
)
# What the league, divisions, season and the declared reports are built from.
DECLARED_OUTPUTS = (
    "fct_line_daily",
    "fct_line_weekly",
    "rpt_weekly_highlights",
    "rpt_monday_morning",
    "rpt_engineering_works",
    "rpt_recovery_time",
    "rpt_division_seeding",
    "rpt_divisions_monthly",
    "rpt_season",
    "rpt_league_strikes_excluded",
    "rpt_weekly_status_mix",
    "int_incidents",
    "dim_lines",
)
MEASURED_OUTPUTS = ("fct_line_measured_daily", "fct_line_weekly_measured")


def _parents() -> dict[str, set[str]]:
    return {
        path.stem: set(REF.findall(path.read_text()))
        for folder in ("models", "tests")
        for path in (TRANSFORM / folder).rglob("*.sql")
    }


def _ancestors(node: str, parents: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(node, ()))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(parents.get(current, ()))
    return seen


def test_every_named_model_exists() -> None:
    parents = _parents()
    for name in (*MEASURED_ROOTS, *DECLARED_OUTPUTS, *MEASURED_OUTPUTS, GATE_TEST):
        assert name in parents, name


def test_declared_outputs_do_not_depend_on_the_measured_path() -> None:
    parents = _parents()
    for model in DECLARED_OUTPUTS:
        leaked = _ancestors(model, parents) & set(MEASURED_ROOTS)
        assert not leaked, f"{model} reads the measured path through {sorted(leaked)}"


def test_gate_failure_skips_only_measured_models() -> None:
    parents = _parents()
    gate_reads = parents[GATE_TEST]
    assert "int_measured_hours" in gate_reads
    # dbt blocks a node when everything the test reads is among its ancestors.
    blocked = {
        node
        for node in parents
        if node != GATE_TEST and not node.startswith("assert_")
        if gate_reads <= _ancestors(node, parents)
    }
    assert set(MEASURED_OUTPUTS) <= blocked
    assert not blocked & set(DECLARED_OUTPUTS)


def test_gate_test_runs_at_the_configured_severity() -> None:
    sql = (TRANSFORM / "tests" / f"{GATE_TEST}.sql").read_text()
    assert "severity=var('gate_test_severity')" in sql
    project = (TRANSFORM / "dbt_project.yml").read_text()
    assert re.search(r"^\s*gate_test_severity:\s*error\s*$", project, re.MULTILINE)
