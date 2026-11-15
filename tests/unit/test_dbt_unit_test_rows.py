"""Offline shape checks for dbt unit tests.

dbt unit tests run only against Athena, so CI never executes them. Athena
builds each fixture as a UNION of its rows, and rows with different columns
fail at runtime ("UNION query has different number of fields"). This catches
that before merge.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

TRANSFORM = Path(__file__).resolve().parents[2] / "transform"


def _fixtures() -> list[tuple[str, str, list[dict[str, Any]]]]:
    found: list[tuple[str, str, list[dict[str, Any]]]] = []
    for path in sorted(TRANSFORM.rglob("*.yml")):
        if "dbt_packages" in path.parts or "target" in path.parts:
            continue
        doc = yaml.safe_load(path.read_text()) or {}
        for test in doc.get("unit_tests") or []:
            parts = [("expect", test.get("expect") or {})]
            parts += [(str(g.get("input")), g) for g in test.get("given") or []]
            for label, part in parts:
                rows = part.get("rows")
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    found.append((f"{test['name']}:{label}", path.name, rows))
    return found


FIXTURES = _fixtures()


def test_unit_tests_were_found() -> None:
    assert len(FIXTURES) > 20


@pytest.mark.parametrize(("name", "file", "rows"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_every_row_has_the_same_columns(name: str, file: str, rows: list[dict[str, Any]]) -> None:
    columns = {tuple(sorted(row)) for row in rows}
    assert len(columns) == 1, f"{file} {name}: rows differ in columns {columns}"
