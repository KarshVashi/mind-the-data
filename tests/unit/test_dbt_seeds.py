"""The dbt seeds that define the declared-view metrics (PLAN 7.1, M3).

These hold metric definitions, so a careless edit changes every score. The
checks here are the ones a dbt data test cannot express simply: agreement
with config/lines.yaml and the plan's weights, and full coverage of time.
"""

import csv
from pathlib import Path

import pytest

from mind_the_data.common.config import load_lines

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "transform" / "seeds"
MINUTES_PER_DAY = 1440
DAY_TYPES = {"mon_thu", "fri", "sat", "sun"}

# PLAN 7.1, by TfL code (ADR-004 has the code table): (unplanned, planned).
PLAN_WEIGHTS = {
    0: (1.0, 1.0),  # Special Service
    2: (8.0, 8.0),  # Suspended
    3: (5.0, 5.0),  # Part Suspended
    4: (0.0, 0.0),  # Planned Closure
    5: (0.0, 0.0),  # Part Closure
    6: (3.0, 3.0),  # Severe Delays
    7: (1.5, 1.5),  # Reduced Service
    8: (5.0, 0.0),  # Bus Service: 0 when planned, otherwise 5
    9: (1.0, 1.0),  # Minor Delays
    10: (0.0, 0.0),  # Good Service
}


def read_seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_lines_seed_matches_config() -> None:
    expected = [
        {
            "line_id": line.id,
            "line_name": line.name,
            "mode_name": line.mode.value,
            "colour": line.colour,
            "is_low_volume": str(line.low_volume).lower(),
        }
        for line in load_lines(ROOT / "config" / "lines.yaml")
    ]
    assert read_seed("lines") == expected


def test_severity_weights_follow_the_plan() -> None:
    rows = {int(row["severity_code"]): row for row in read_seed("severity_weights")}
    for code, (unplanned, planned) in PLAN_WEIGHTS.items():
        row = rows[code]
        assert (float(row["weight_unplanned"]), float(row["weight_planned"])) == (
            unplanned,
            planned,
        ), code
        assert row["source"] == "plan"
    closures = {code for code, row in rows.items() if row["is_closure"] == "true"}
    assert closures == {4, 5}
    # Anything beyond the plan needs a recorded decision, never a silent default.
    for code in rows.keys() - PLAN_WEIGHTS.keys():
        assert rows[code]["source"].startswith("decision_"), code


@pytest.mark.parametrize("day_class", ["weekday", "weekend"])
def test_time_bands_cover_each_day_exactly_once(day_class: str) -> None:
    bands = sorted(
        (int(row["band_start_minute"]), int(row["band_end_minute"]))
        for row in read_seed("time_bands")
        if row["day_class"] == day_class
    )
    edge = 0
    for start, end in bands:
        assert start == edge
        assert end > start
        edge = end
    assert edge == MINUTES_PER_DAY


def test_time_band_weights_follow_the_plan() -> None:
    weights = {
        (row["day_class"], int(row["band_start_minute"])): float(row["time_weight"])
        for row in read_seed("time_bands")
    }
    assert weights[("weekday", 7 * 60)] == 2.0
    assert weights[("weekday", 16 * 60)] == 2.0
    assert weights[("weekend", 0)] == 0.75
    peaks = {7 * 60, 16 * 60}
    off_peak = [w for (day, start), w in weights.items() if day == "weekday" and start not in peaks]
    assert off_peak and set(off_peak) == {1.0}


def test_day_types_map_every_iso_day() -> None:
    rows = read_seed("day_types")
    assert [int(row["iso_day_of_week"]) for row in rows] == list(range(1, 8))
    assert {row["day_type"] for row in rows} == DAY_TYPES
    weekend = {int(row["iso_day_of_week"]) for row in rows if row["day_class"] == "weekend"}
    assert weekend == {6, 7}


def test_operating_hours_cover_every_line_on_weekdays() -> None:
    rows = read_seed("line_operating_hours")
    keys = [(row["line_id"], row["day_type"]) for row in rows]
    assert len(keys) == len(set(keys))
    line_ids = {line.id for line in load_lines(ROOT / "config" / "lines.yaml")}
    for line_id in line_ids:
        assert (line_id, "mon_thu") in keys, line_id
        assert (line_id, "fri") in keys, line_id
    for row in rows:
        start, end = int(row["service_start_minute"]), int(row["service_end_minute"])
        assert row["line_id"] in line_ids
        assert row["day_type"] in DAY_TYPES
        assert 0 <= start < end
        # A service day may run past midnight, never past the next day's.
        assert end - start <= 2 * MINUTES_PER_DAY
        assert row["source"]
