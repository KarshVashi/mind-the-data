"""The Iceberg maintenance macro's SQL, rendered offline with plain Jinja.

dbt's `return` is stood in for by an exception that carries the value, and
`modules` / `exceptions` by the pieces the two pure macros use. The macro
that talks to Athena (iceberg_maintenance) is left to `dbt parse` and the
weekly run.
"""

import datetime
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jinja2
import pytest
import yaml

TRANSFORM = Path(__file__).resolve().parents[2] / "transform"
MACROS = (TRANSFORM / "macros" / "iceberg_maintenance.sql").read_text()


class Returned(Exception):
    def __init__(self, value: Any) -> None:
        self.value = value


class CompilerError(Exception):
    pass


def _return(value: Any) -> None:
    raise Returned(value)


def _raise(message: str) -> None:
    raise CompilerError(message)


def call(macro: str, *args: Any) -> Any:
    env = jinja2.Environment(extensions=["jinja2.ext.do"])
    template = env.from_string(MACROS + "{{ " + macro + "(*args) }}")
    try:
        template.render(
            args=args,
            modules=SimpleNamespace(re=re, datetime=datetime),
            exceptions=SimpleNamespace(raise_compiler_error=_raise),
            **{"return": _return},
        )
    except Returned as done:
        return done.value
    raise AssertionError(f"{macro} did not return")


def node(name: str, materialized: str, table_type: str = "iceberg", **config: Any) -> Any:
    cfg = {"enabled": True, "materialized": materialized, "table_type": table_type, **config}
    return SimpleNamespace(name=name, resource_type="model", config=cfg)


def test_bounded_optimize_then_vacuum() -> None:
    assert call(
        "iceberg_maintenance_sql", "mind_the_data_prod", "stg_arrivals", ["dt"], "2026-11-06"
    ) == [
        (
            "optimize mind_the_data_prod.stg_arrivals rewrite data using bin_pack "
            "where dt >= date '2026-11-06'"
        ),
        "vacuum mind_the_data_prod.stg_arrivals",
    ]


def test_full_compaction_has_no_where() -> None:
    assert call("iceberg_maintenance_sql", "mind_the_data_prod", "stg_polls", ["dt"], None) == [
        "optimize mind_the_data_prod.stg_polls rewrite data using bin_pack",
        "vacuum mind_the_data_prod.stg_polls",
    ]


def test_unpartitioned_or_transformed_partitions_compact_everything() -> None:
    for partitioned_by in (None, [], ["day(minute_utc)"]):
        sql = call("iceberg_maintenance_sql", "db", "t", partitioned_by, "2026-11-06")
        assert sql[0] == "optimize db.t rewrite data using bin_pack"


@pytest.mark.parametrize(
    ("schema", "identifier", "since"),
    [
        ("prod; drop", "t", None),
        ("db", "T", None),
        ("db", "t\n", None),
        ("db", "t", "2026-11-06' or 1=1 --"),
    ],
)
def test_unsafe_names_and_dates_are_refused(
    schema: str, identifier: str, since: str | None
) -> None:
    with pytest.raises(CompilerError):
        call("iceberg_maintenance_sql", schema, identifier, ["dt"], since)


def test_only_enabled_incremental_iceberg_models_are_targets() -> None:
    nodes = [
        node("stg_polls", "incremental"),
        node("int_line_minute_status", "table"),
        node("stg_arrivals", "incremental"),
        node("stg_hive", "incremental", table_type="hive"),
        node("stg_off", "incremental", enabled=False),
        SimpleNamespace(name="seed_x", resource_type="seed", config={"materialized": "seed"}),
    ]
    assert [n.name for n in call("iceberg_maintenance_targets", nodes)] == [
        "stg_arrivals",
        "stg_polls",
    ]


def test_lookback_covers_a_week_of_merges() -> None:
    project = yaml.safe_load((TRANSFORM / "dbt_project.yml").read_text())
    variables = project["vars"]
    assert variables["maintenance_lookback_days"] >= 7 + variables["lookback_days"]
