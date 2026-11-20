"""Critical links (ADR-020) on a hand-made network.

Line T runs A - B - C, then branches C - D and C - E. Line U runs A - B
alongside T, then B - X - Y - D. So T's A - B has a parallel line, C - D a
way round of four stops (C B X Y D), and C - E none at all.
"""

import pytest

from mind_the_data.demand.network import (
    Link,
    Section,
    StatusSpell,
    coverage,
    exposure,
    graph,
    hour_shares,
    links_from_seed,
    name_index,
    normalise,
    rest_of_line_weight,
    sections,
    stops_without,
)

NAMES = {
    "A": "Alpha LU",
    "B": "Beta",
    "C": "Gamma Road",
    "D": "Delta Park",
    "E": "Epsilon (Branch)",
    "X": "Ex",
    "Y": "Why",
}


def seed_row(line: str, a: str, b: str, mon_thu: float = 0.0) -> dict[str, str]:
    return {
        "numbat_line": line,
        "from_asc": a,
        "from_station": NAMES[a],
        "to_asc": b,
        "to_station": NAMES[b],
        "mon_thu_load": str(mon_thu),
        "fri_load": "0",
        "sat_load": "0",
        "sun_load": "0",
        "am_peak_load": "0",
    }


SEED = [
    seed_row("T", "A", "B"),
    seed_row("T", "B", "C"),
    seed_row("T", "C", "D", mon_thu=700),
    seed_row("T", "D", "C", mon_thu=500),
    seed_row("T", "C", "E"),
    seed_row("U", "A", "B"),
    seed_row("U", "B", "X"),
    seed_row("U", "X", "Y"),
    seed_row("U", "Y", "D"),
]
LINKS = links_from_seed(SEED)
NETWORK = graph(LINKS.values())
T_GRAPH = graph(link for link in LINKS.values() if link.numbat_line == "T")
T_NAMES = name_index(LINKS.values())["T"]


def link(a: str, b: str, line: str = "T") -> Link:
    first, second = sorted((a, b))
    return LINKS[(line, first, second)]


def test_directions_fold_into_one_link() -> None:
    assert link("C", "D").loads["mon_thu"] == 1200
    assert len(LINKS) == 8


@pytest.mark.parametrize(("a", "b", "stops"), [("A", "B", 1), ("C", "D", 4), ("C", "E", None)])
def test_stops_without_the_link(a: str, b: str, stops: int | None) -> None:
    assert stops_without(NETWORK, link(a, b)) == stops


def test_normalise_drops_suffixes_and_brackets() -> None:
    assert normalise("King's Cross St. Pancras") == "kings cross st pancras"
    assert normalise("Epsilon (Branch)") == "epsilon"
    assert normalise("Heathrow Terminals/Reading") == "heathrow terminals / reading"


def test_name_index_accepts_unique_leading_words() -> None:
    assert T_NAMES["alpha"] == "A"
    assert T_NAMES["gamma road"] == "C"
    # "gamma" alone is one word, so it is not a prefix key.
    assert "gamma" not in T_NAMES


def test_sections_read_alternatives_and_phrases() -> None:
    found, unmatched = sections(
        "No service between Gamma Road and Epsilon. SEVERE DELAYS between Alpha to Delta Park"
        " / Why.",
        T_NAMES,
    )
    assert not unmatched
    assert found == [
        Section(start="C", ends=("E",), phrase="no service"),
        Section(start="A", ends=("D",), phrase="severe delays"),
    ]
    _, unmatched = sections("Minor delays between Nowhere and Gamma Road", T_NAMES)
    assert unmatched


def cover(
    a: str, b: str, reason: str, status: str = "Part Suspended", weight: float = 5
) -> float | None:
    result = coverage(link(a, b), reason, status, weight, T_GRAPH, T_NAMES)
    return result.weight if result.placed else None


def test_coverage_follows_each_section_on_the_branching_line() -> None:
    reason = (
        "No service between Gamma Road and Epsilon. SEVERE DELAYS between Alpha and Gamma Road."
    )
    assert cover("C", "E", reason) == 5
    assert cover("A", "B", reason) == 3  # severe delays, capped by nothing
    assert cover("C", "D", reason) == 0  # outside both sections


def test_coverage_whole_line_rest_of_line_and_unplaced() -> None:
    assert cover("C", "D", "Minor delays due to train cancellations.", "Minor Delays", 1) == 1
    assert cover("C", "D", "Part suspended while we fix a signal failure.") is None
    rest = "Severe delays between Alpha and Beta. MINOR DELAYS the rest of the line."
    assert cover("C", "D", rest, "Severe Delays", 3) == 1
    assert rest_of_line_weight("GOOD SERVICE on the rest of the line") == 0


def test_exposure_is_severity_times_typical_passengers() -> None:
    shares = hour_shares(
        [
            {
                "numbat_line": "T",
                "service_day_type": "mon_thu",
                "hour_of_day": "8",
                "link_load_sum": "300",
            },
            {
                "numbat_line": "T",
                "service_day_type": "mon_thu",
                "hour_of_day": "9",
                "link_load_sum": "100",
            },
        ]
    )
    assert shares[("T", "mon_thu", 8)] == 0.75

    def spell(line_id: str, status: str, reason: str, weight: float, minutes: int) -> StatusSpell:
        return StatusSpell(line_id, status, reason, weight, "mon_thu", 8, minutes)

    spells = [
        spell("t1", "Severe Delays", "Severe delays due to a signal failure.", 3, 10),
        spell("t1", "Part Suspended", "No service while we fix a signal failure.", 5, 5),
        spell("t2", "Suspended", "No service.", 8, 60),  # a line not serving T
        spell("t3", "Minor Delays", "Minor delays.", 1, 4),
    ]
    result = exposure(link("C", "D"), spells, {"t1": 1.0, "t3": 0.5}, shares, T_GRAPH, T_NAMES)
    # 1,200 a day x 0.75 of the day in hour 8 / 60 = 15 passengers a minute.
    # t1: 3 x 15 x 10 = 450; t3: 1 x 15 x 4 x 0.5 = 30.
    assert result.passenger_weighted == pytest.approx(480)
    assert result.covered_minutes == 14
    assert result.heavy_minutes == 0
    assert result.unplaced_minutes == 5
