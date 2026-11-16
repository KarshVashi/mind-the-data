"""Which tables the stale tmp cleanup drops, rendered offline with plain Jinja.

The macro that lists and drops tables in Athena is left to `dbt parse` and the
daily run; this covers the name filter that decides what goes.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jinja2
import pytest

MACROS = (
    Path(__file__).resolve().parents[2] / "transform" / "macros" / "drop_stale_tmp_relations.sql"
).read_text()


class Returned(Exception):
    def __init__(self, value: Any) -> None:
        self.value = value


def _return(value: Any) -> None:
    raise Returned(value)


def stale(names: list[str]) -> Any:
    env = jinja2.Environment(extensions=["jinja2.ext.do"])
    template = env.from_string(MACROS + "{{ stale_tmp_identifiers(names) }}")
    try:
        template.render(names=names, modules=SimpleNamespace(re=re), **{"return": _return})
    except Returned as done:
        return done.value
    raise AssertionError("stale_tmp_identifiers did not return")


def test_only_tmp_tables_are_picked_sorted() -> None:
    names = [
        "rpt_divisions_monthly_promotion_and_relegation__dbt_tmp",
        "rpt_divisions_monthly_promotion_and_relegation",
        "fct_line_weekly",
        "stg_arrivals__dbt_tmp",
    ]
    assert stale(names) == [
        "rpt_divisions_monthly_promotion_and_relegation__dbt_tmp",
        "stg_arrivals__dbt_tmp",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "fct_line_weekly",
        "__dbt_tmp",
        "fct_line_weekly__dbt_tmp_backup",
        "fct_line_weekly__ha",
        "dbt_tmp",
        "fct__dbt_tmp; drop table fct_line_weekly",
    ],
)
def test_other_tables_are_kept(name: str) -> None:
    assert stale([name]) == []


def test_nothing_to_drop() -> None:
    assert stale([]) == []
