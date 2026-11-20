"""Contracts on the dbt marts that publishing reads (ADR-017).

dbt-athena checks a contract's column names and broad types when it builds the
table, so a mart can no longer drift from what `publish/marts.py` selects
without the build failing. These tests keep the other side in step: every
column the export reads is declared, and every mart it reads is contracted.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from mind_the_data.publish.marts import queries

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "transform" / "models" / "marts" / "_schema.yml"
MART_PREFIXES = ("dim_", "fct_", "rpt_")
ATHENA_TYPES = {"varchar", "bigint", "integer", "double", "boolean", "date", "timestamp"}


def _models() -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in yaml.safe_load(SCHEMA.read_text())["models"]}


def _columns_read() -> dict[str, set[str]]:
    """Columns each mart is read for, parsed from the export's own SELECTs."""
    read: dict[str, set[str]] = {}
    for sql in queries("db", date(2026, 1, 5), date(2026, 1, 11)).values():
        aliases = {
            alias or name: name
            for name, alias in re.findall(r"(?:from|join) db\.(\w+)(?: as (\w+))?", sql)
        }
        marts = {name for name in aliases.values() if name.startswith(MART_PREFIXES)}
        for name in marts:
            read.setdefault(name, set())
        # Joined queries qualify every column with its table's alias.
        for alias, column in re.findall(r"\b(\w+)\.(\w+)\b", sql):
            if aliases.get(alias) in marts:
                read[aliases[alias]].add(column)
        # `select (select max(built_at) from db.fct_line_weekly) as ...`
        for column, name in re.findall(r"max\((\w+)\) from db\.(\w+)", sql):
            if name in marts:
                read[name].add(column)
        # A single-table query lists bare column names between select and from.
        if len(marts) == 1 and "join" not in sql:
            select_list = re.match(r"select (.*?) from db\.", sql)
            if select_list:
                (name,) = marts
                read[name].update(
                    item for item in select_list.group(1).split(", ") if re.fullmatch(r"\w+", item)
                )
    return read


READ = _columns_read()


def test_the_parser_found_the_marts_it_should() -> None:
    # Guards the regexes above: if queries() changes shape, fail here rather
    # than pass the tests below on an empty set.
    assert {"fct_line_daily", "fct_line_weekly", "fct_line_weekly_measured", "dim_lines"} <= set(
        READ
    )
    assert {"week_start", "disruption_rank", "built_at"} <= READ["fct_line_weekly"]
    assert {"honesty_gap_hours", "experienced_rank"} <= READ["fct_line_weekly_measured"]


@pytest.mark.parametrize("model", sorted(READ))
def test_every_mart_the_export_reads_enforces_a_contract(model: str) -> None:
    config = _models()[model].get("config") or {}
    assert (config.get("contract") or {}).get("enforced") is True, (
        f"{model} is read by publish/marts.py but has no enforced contract"
    )


@pytest.mark.parametrize("model", sorted(READ))
def test_contract_declares_every_column_the_export_reads(model: str) -> None:
    declared = {c["name"] for c in _models()[model]["columns"]}
    assert READ[model] <= declared, f"{model} lacks {sorted(READ[model] - declared)}"


@pytest.mark.parametrize("model", sorted(READ))
def test_every_contracted_column_has_a_type(model: str) -> None:
    for column in _models()[model]["columns"]:
        assert column.get("data_type") in ATHENA_TYPES | {"array(varchar)"}, (
            f"{model}.{column['name']} needs a data_type"
        )
