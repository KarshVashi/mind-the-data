"""The disruption-cause patterns (D74) and the rule that applies them.

The seed is loaded by dbt-athena through OpenCSVSerde, which drops a
backslash. The patterns therefore use `(^|[^a-z])` boundaries and are
checked here with Python's `re` under the rule int_status_causes follows:
the cause whose match starts first wins, a tie goes to the lower priority.
The SQL itself is covered by dbt unit tests and a singular test against the
loaded seed.
"""

import csv
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "transform" / "seeds"
NO_CAUSE = "No cause given"


def read_patterns() -> list[dict[str, str]]:
    with (SEEDS / "disruption_cause_patterns.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def named_cause(reason: str | None) -> str:
    """The same normalisation and rule as int_status_causes."""
    text = " " + re.sub(r"\s+", " ", (reason or "").replace("|", " ")).strip().lower() + " "
    best: tuple[int, int, str] | None = None
    for row in read_patterns():
        found = re.search(row["pattern"], text)
        if found:
            candidate = (found.start(), int(row["priority"]), row["cause"])
            if best is None or candidate < best:
                best = candidate
    return best[2] if best else NO_CAUSE


@pytest.mark.parametrize("path", sorted(SEEDS.glob("*.csv")), ids=lambda p: p.name)
def test_seed_csv_has_no_backslash(path: Path) -> None:
    # OpenCSVSerde's default escape character is the backslash: it is dropped
    # on load, so a pattern that uses one silently stops matching.
    assert "\\" not in path.read_text(), f"{path.name} contains a backslash"


def test_patterns_are_unique_prioritised_and_compile() -> None:
    rows = read_patterns()
    assert [int(r["priority"]) for r in rows] == list(range(1, len(rows) + 1))
    assert len({r["cause"] for r in rows}) == len(rows)
    assert NO_CAUSE not in {r["cause"] for r in rows}
    for row in rows:
        re.compile(row["pattern"])
        # Every pattern starts at a word boundary, so "bonfire" is not "fire".
        assert row["pattern"].startswith("(^|[^a-z])"), row["cause"]


@pytest.mark.parametrize(
    ("reason", "cause"),
    [
        ("Central Line: Minor delays due to train cancellations.", "Train cancellations"),
        (
            "No service between Barking and Barking Riverside while we fix a points failure.",
            "Points failures",
        ),
        ("Minor delays due to an earlier signalling problem at Rickmansworth", "Signal failures"),
        ("Severe delays due to an earlier faulty train at Westminster.", "Faulty trains"),
        ("Severe delays due to a track fault at Brixton.", "Track faults"),
        ("Minor delays due to an earlier customer incident.", "Customer incidents and illness"),
        (
            "Severe delays due to an earlier animal on the track.",
            "People, animals or objects on the track",
        ),
        ("due to the unavailability of depot staff.", "Staff unavailable"),
        (
            "Minor delays due to an earlier fire near the track at Hanger Lane.",
            "Fire alerts and fires",
        ),
        ("Minor delays while we fix an electrical fault.", "Power and electrical faults"),
        ("due to a road traffic collision at West Croydon.", "Road and bridge incidents"),
        ("No service due to planned engineering works.", "Engineering works and overruns"),
        ("Minor delays due to flooding at the station.", "Flooding and weather"),
        (
            "No Service while we respond to a security alert.",
            "Emergency services and security alerts",
        ),
        (
            "a reduced service will operate between Gospel Oak and Woodgrange Park.",
            "Planned service changes",
        ),
        ("the 2159 train from Stratford will terminate at Camden Road.", "Planned service changes"),
        ("Minor delays while we fix faulty platform screen doors.", "Platform screen door faults"),
        ("Minor delays while we conduct a track inspection at Queensbury.", "Track inspections"),
        ("Severe delays due to late running of trains.", "Late running"),
        (
            "Minor delays due to congestion caused by disruption on the Mildmay line.",
            "Congestion from other lines",
        ),
        # The first cause TfL names wins, whatever its priority number.
        (
            "Severe delays while we fix a signal failure and due to train cancellations.",
            "Signal failures",
        ),
        (
            "Minor delays due to an earlier faulty train at Richmond and train cancellations.",
            "Faulty trains",
        ),
        (
            "Minor delays due to train cancellations. Minor Delays while we fix a signal failure.",
            "Train cancellations",
        ),
        # Case, doubled spaces and a bar do not matter.
        (
            "VICTORIA  LINE: SEVERE DELAYS due to a   POINTS | FAILURE at Brixton.",
            "Points failures",
        ),
        # No cause named.
        ("Minor delays due to an earlier fault at South Kenton.", NO_CAUSE),
        ("Minor delays between Battersea Power Station and Kennington.", NO_CAUSE),
        ("Minor delays due to smoke from a bonfire.", NO_CAUSE),
        ("", NO_CAUSE),
        (None, NO_CAUSE),
    ],
)
def test_first_named_cause(reason: str | None, cause: str) -> None:
    assert named_cause(reason) == cause


def test_unit_test_patterns_are_the_seed_patterns() -> None:
    """The dbt unit test copies the patterns it needs; they must not drift."""
    document = yaml.safe_load(
        (ROOT / "transform/models/intermediate/_unit_tests_causes.yml").read_text()
    )
    seed = {row["cause"]: row for row in read_patterns()}
    copied = [
        row
        for unit_test in document["unit_tests"]
        for given in unit_test["given"]
        if given["input"] == "ref('disruption_cause_patterns')"
        for row in given["rows"]
    ]
    assert copied
    for row in copied:
        assert row["pattern"] == seed[row["cause"]]["pattern"], row["cause"]
        assert row["priority"] == int(seed[row["cause"]]["priority"]), row["cause"]
