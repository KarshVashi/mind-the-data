"""The Python half of the arrival parity test (PLAN M4, task 2).

The dbt unit test ``int_inferred_arrivals_matches_the_reference``
(transform/models/intermediate/_arrival_parity.yml) feeds the SQL model the
same fixture and expects the same file. Together they hold the two
implementations to identical arrivals on 10 minutes of real boards plus a
synthetic station (940GZZTEST) built on the rules' boundaries.
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


def _successful_polls() -> list[dict[str, str]]:
    # The SQL model filters stg_polls on is_ok; the reference takes only
    # successful polls.
    return [row for row in _read("polls") if row["is_ok"] == "true"]


def test_reference_reproduces_the_expected_arrivals() -> None:
    arrivals = infer_arrivals_from_rows(_read("predictions"), _successful_polls(), PARAMS)
    assert [_as_text(row) for row in arrivals] == _read("expected")


def test_fixture_exercises_every_rule() -> None:
    """The fixture is only useful if it covers where the two implementations
    could drift: vehicle-id linking (Victoria), expected-arrival linking
    (Central), a shared prediction id without vehicle ids (DLR), both
    arrival rules, stale time to station, and on the synthetic station a
    failed poll, an empty board, a placeholder id, two id candidates, an
    expected arrival before the poll and values exactly on 15 s, 45 s and
    120 s."""
    expected = _read("expected")
    assert {row["line_id"] for row in expected} == {"victoria", "central", "dlr"}
    assert {row["rule"] for row in expected} == {"left_board", "at_platform"}
    assert any(row["last_time_to_station"] != row["last_seconds_to_arrival"] for row in expected)

    test_polls = [r for r in _read("polls") if "940GZZTEST" in r["request_path"]]
    test_rows = [r for r in _read("predictions") if "940GZZTEST" in r["request_path"]]
    assert any(r["is_ok"] == "false" for r in test_polls)
    assert {r["poll_id"] for r in test_polls} - {r["poll_id"] for r in test_rows}, "an empty board"
    assert {"000", "201", "202"} <= {r["vehicle_id"] for r in test_rows}
    synthetic = {
        (r["platform_name"], r["last_prediction_id"]): r
        for r in expected
        if "940GZZTEST" in r["request_path"]
    }
    # 120 s to go exactly is an arrival; the 14 s duplicate is gone.
    assert synthetic[("Dup", "d3")]["last_seconds_to_arrival"] == "120"
    # Due 10 s before the poll: floored to 0, at the platform.
    assert synthetic[("Due", "u1")]["rule"] == "at_platform"
    # A 45 s move links, so m1 ends once, in the later poll.
    assert synthetic[("Match", "m1")]["last_poll_id"] == "poll-906"
    # The placeholder id does not link v1 to v2: two arrivals.
    assert {("Veh", "v1"), ("Veh", "v2")} <= synthetic.keys()
