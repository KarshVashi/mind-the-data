"""The Python half of the arrival parity test (PLAN M4, task 2).

The dbt unit test ``int_inferred_arrivals_matches_the_reference``
(transform/models/intermediate/_arrival_parity.yml) feeds the SQL model the
same fixture and expects the same file. Together they hold the two
implementations to identical arrivals on 15 minutes of real boards.
"""

import csv
from pathlib import Path

from mind_the_data.analysis.arrivals import (
    ARRIVAL_COLUMNS,
    InferenceParams,
    infer_arrivals_from_rows,
)

FIXTURES = Path(__file__).resolve().parents[2] / "transform" / "tests" / "fixtures"
PARAMS = InferenceParams(vehicle_id_lines=frozenset({"victoria"}))


def _read(name: str) -> list[dict[str, str]]:
    with (FIXTURES / f"arrival_parity_{name}.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def _as_text(row: dict[str, object]) -> dict[str, str]:
    text = {column: "" if row[column] is None else str(row[column]) for column in ARRIVAL_COLUMNS}
    text["arrival_time"] = text["arrival_time"].removesuffix("+00:00")
    return text


def test_reference_reproduces_the_expected_arrivals() -> None:
    arrivals = infer_arrivals_from_rows(_read("predictions"), _read("polls"), PARAMS)
    assert [_as_text(row) for row in arrivals] == _read("expected")


def test_fixture_exercises_every_rule() -> None:
    """The fixture is only useful if it covers what differs between lines:
    vehicle-id linking (Victoria), expected-arrival linking (Central), a
    shared prediction id without vehicle ids (DLR), and both arrival rules."""
    expected = _read("expected")
    assert {row["line_id"] for row in expected} == {"victoria", "central", "dlr"}
    assert {row["rule"] for row in expected} == {"left_board", "at_platform"}
    assert any(row["last_time_to_station"] != row["last_seconds_to_arrival"] for row in expected)
