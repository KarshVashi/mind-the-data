"""The dbt seeds that define the metrics (PLAN 7.1 and 7.3, M3 and M4).

These hold metric definitions, so a careless edit changes every score. The
checks here are the ones a dbt data test cannot express simply: agreement
with config/lines.yaml, config/stations.yaml and the plan's weights, and
full coverage of time.
"""

import csv
import re
from pathlib import Path

import pytest

from mind_the_data.common.config import load_lines, load_stations

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


def test_works_when_planned_codes_are_the_suspensions_and_severe_delays() -> None:
    rows = read_seed("severity_weights")
    works = {int(row["severity_code"]) for row in rows if row["is_works_when_planned"] == "true"}
    # D69: Suspended, Part Suspended, Severe Delays. Never a closure, which is
    # engineering works whatever its filing.
    assert works == {2, 3, 6}
    assert all(row["is_works_when_planned"] in {"true", "false"} for row in rows)


def _reason_signals(reason: str) -> set[str]:
    # The model lower-cases the reason; Athena's regexp_like finds a match
    # anywhere, as re.search does, and these patterns use only groups,
    # alternation, optional items and character classes, on which Python re
    # and Trino agree. Avoid POSIX classes such as [[:space:]].
    return _signals(reason.lower(), {"planned_works", "overrun"})


def _signals(text: str, wanted: set[str]) -> set[str]:
    return {
        row["signal"]
        for row in read_seed("planned_works_reasons")
        if row["signal"] in wanted and re.search(row["pattern"], text)
    }


def _reason_text_basis(reason: str) -> bool:
    # Mirrors int_line_minute_status (D69): split the lower-cased reason at
    # every full stop not followed by a digit, as Athena's regexp_split does.
    # Planned works when some sentence says so and every disruption sentence
    # says so; an overrun anywhere wins.
    sentences = [
        _signals(s, {"planned_works", "overrun", "disruption"})
        for s in re.split("[.](?![0-9])", reason.lower())
    ]
    if any("overrun" in sig for sig in sentences):
        return False
    return any("planned_works" in sig for sig in sentences) and all(
        "planned_works" in sig for sig in sentences if "disruption" in sig
    )


@pytest.mark.parametrize(
    ("reason", "signals"),
    [
        ("No service between A and B due to planned engineering work.", {"planned_works"}),
        ("Severe delays due to Planned Engineering Works.", {"planned_works"}),
        ("Part suspended because of planned works this weekend.", {"planned_works"}),
        ("Part suspended during a planned closure of the branch.", {"planned_works"}),
        ("Severe delays due to an earlier signal failure.", set()),
        ("Part suspended after unplanned engineering work on the track.", set()),
        ("Reduced service; trains later than planned.", {"overrun"}),
        (
            "Part suspended; planned works taking longer than expected.",
            {"overrun", "planned_works"},
        ),
        ("Severe delays; works taking longer than planned.", {"overrun"}),
        ("Part suspended; works finish later than expected.", {"overrun"}),
        ("Part suspended; engineering work not yet completed.", {"overrun"}),
        ("Part suspended; engineering work has not been finished.", {"overrun"}),
        ("Part suspended; works finishes late.", {"overrun"}),
        ("Part suspended; works finishing late.", {"overrun"}),
        ("Severe delays due to engineering works over running.", {"overrun"}),
        ("Minor delays due to planned engineering work", {"planned_works"}),
        ("Unplanned engineering work", set()),
        ("Overrunning engineering works", {"overrun"}),
        ("Engineering works which are taking longer than expected", {"overrun"}),
        ("Works finishes late", {"overrun"}),
        ("Severe delays due to overrunning engineering works.", {"overrun"}),
        (
            "Severe delays due to over-running planned engineering work.",
            {"overrun", "planned_works"},
        ),
        ("Part suspended as engineering works over run.", {"overrun"}),
        ("Engineering work overran at Gospel Oak.", {"overrun"}),
        ("Delays after a late finish of engineering works.", {"overrun"}),
        ("Delays because engineering works finished late.", {"overrun"}),
        (
            "Part suspended as planned engineering work is not complete.",
            {"overrun", "planned_works"},
        ),
        ("Severe delays; engineering work not yet complete at Neasden.", {"overrun"}),
        ("Part suspended; engineering work will not be completed until 07:00.", {"overrun"}),
        ("Severe delays as engineering work would not be finished on time.", {"overrun"}),
        ("Part suspended; engineering works taking longer to complete.", {"overrun"}),
        ("Severe delays as works are taking longer to finish.", {"overrun"}),
        # A word that only starts like "complete" is not an overrun.
        ("Minor delays; not completely clear of the platform.", set()),
    ],
)
def test_planned_works_reason_patterns(reason: str, signals: set[str]) -> None:
    assert _reason_signals(reason) == signals


def test_planned_works_reason_signals_are_known() -> None:
    rows = read_seed("planned_works_reasons")
    assert {row["signal"] for row in rows} == {"planned_works", "overrun", "disruption"}
    for row in rows:
        assert row["pattern"] == row["pattern"].lower(), row
        re.compile(row["pattern"])


@pytest.mark.parametrize(
    ("sentence", "is_disruption"),
    [
        ("no service between a and b", True),
        ("no northbound service between a and b", True),
        ("part suspended between a and b", True),
        ("the line is suspended", True),
        ("severe delays due to a signal failure", True),
        ("minor delays", True),
        ("trains are delayed", True),
        ("bank station is closed", True),
        ("reduced service on the branch", True),
        ("part closure between a and b", True),
        ("no piccadilly line service between a and b", True),
        ("no hammersmith & city line service between a and b", True),
        ("no northbound or southbound service between a and b", True),
        ("no step-free / lift service at c", True),
        ("no trains between a and b", True),
        ("trains are not stopping at c", True),
        ("trains will not stop at c", True),
        ("trains will not run between a and b", True),
        ("trains are not calling at c", True),
        ("fewer trains than usual", True),
        ("severedelays between a and b", True),
        ("partsuspended", True),
        ("partclosure", True),
        ("buses replace trains between a and b", True),
        ("rail replacement buses run between a and b", True),
        ("good service on the rest of the line", False),
        ("tickets will be accepted on london buses via any reasonable route", False),
        # Word edges: neither "closedown" nor "delayering" is a disruption.
        ("closedown notice", False),
        ("delayering works", False),
        # Five words between "no" and "service" is past the limit.
        ("no one two three four five service", False),
        ("no customer services", False),
        ("trains are running normally", False),
    ],
)
def test_disruption_sentence_patterns(sentence: str, is_disruption: bool) -> None:
    assert (_signals(sentence, {"disruption"}) == {"disruption"}) is is_disruption


@pytest.mark.parametrize(
    ("reason", "basis"),
    [
        # Real W46 reasons (D69, sentence rule).
        (
            (
                "Northern Line: No service between Battersea Power Station/Morden and Charing "
                "Cross/Euston via Bank due to planned engineering works. GOOD SERVICE on the "
                "rest of the line."
            ),
            True,
        ),
        (
            (
                "Northern Line: No service between Morden and Charing cross due to planned "
                "engineering works. GOOD SERVICE on the rest of the line."
            ),
            True,
        ),
        (
            (
                "Northern Line: No service between Colindale and Edgware while we fix a signal "
                "failure at Edgware. Tickets will be accepted on London Buses via any reasonable "
                "route. NO SERVICE between Battersea Power Station/Morden and Charing Cross/Euston "
                "via Bank due to planned engineering works. GOOD SERVICE on the rest of the line."
            ),
            False,
        ),
        (
            (
                "Windrush Line: No service between Surrey Quays and New Cross Gate while we help "
                "a person taken ill on a train. NO SERVICE between New Cross Gate to Crystal "
                "Palace and West Croydon due to planned engineering works. GOOD SERVICE on the "
                "rest of the line."
            ),
            False,
        ),
        (
            (
                "Windrush Line: No service between Surrey Quays and Clapham Junction due to a "
                "points failure at Denmark Hill. No service between New Cross Gate and Crystal "
                "Palace / West Croydon due to planned engineering work. GOOD SERVICE on the rest "
                "of the line."
            ),
            False,
        ),
        # One sentence, works wording.
        ("Part suspended due to planned engineering work.", True),
        # Works wording but no disruption sentence at all still counts.
        ("Planned engineering works this weekend.", True),
        # An overrun in another sentence wins.
        (
            (
                "No service between A and B due to planned engineering works. "
                "Engineering works are overrunning at C."
            ),
            False,
        ),
        # No works wording.
        ("Severe delays due to an earlier signal failure.", False),
        # A line named between "no" and "service": the incident is still a
        # disruption sentence without works wording.
        (
            (
                "No Piccadilly line service between A and B while we fix a signal failure. "
                "No service between C and D due to planned engineering works."
            ),
            False,
        ),
        (
            (
                "No Hammersmith & City line service between A and B while we fix a signal "
                "failure. No service between C and D due to planned engineering works."
            ),
            False,
        ),
        (
            (
                "No northbound or southbound service at A after a customer incident. "
                "No service between C and D due to planned engineering works."
            ),
            False,
        ),
        # Sentences TfL runs together: CRLF, no space, two spaces.
        (
            (
                "Severe delays due to a signal failure.\r\nNo service between C and D due to "
                "planned engineering works."
            ),
            False,
        ),
        (
            (
                "Severe delays due to a signal failure.No service between C and D due to "
                "planned engineering works."
            ),
            False,
        ),
        (
            (
                "Severe delays due to a signal failure.  No service between C and D due to "
                "planned engineering works."
            ),
            False,
        ),
        # A time written with a full stop does not end the sentence.
        ("No service between A and B until 08.15 due to planned engineering work.", True),
        # Rail replacement wording inside the works sentence is still works.
        (
            "No service between A and B due to planned engineering works; rail replacement buses run.",
            True,
        ),
    ],
)
def test_reason_text_basis_sentence_rule(reason: str, basis: bool) -> None:
    assert _reason_text_basis(reason) is basis


@pytest.mark.parametrize("path", sorted(SEEDS.glob("*.csv")), ids=lambda p: p.name)
def test_seed_csv_has_no_backslash(path: Path) -> None:
    # dbt-athena loads seeds through OpenCSVSerde, whose default escape
    # character is the backslash: it is dropped on load, so a regular
    # expression using it silently stops matching.
    assert "\\" not in path.read_text(), f"{path.name} contains a backslash"


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


def test_schedule_day_types_are_known_and_unique() -> None:
    rows = read_seed("timetable_schedule_day_types")
    pairs = [(row["schedule_name"], row["day_type"]) for row in rows]
    assert len(pairs) == len(set(pairs))
    assert {day_type for _, day_type in pairs} == DAY_TYPES


def test_single_weekday_schedules_fall_in_their_day_type() -> None:
    """A schedule with an ISO weekday applies on that weekday only (D67), so
    the weekday must belong to the schedule's day type."""
    weekday_types = {int(row["iso_day_of_week"]): row["day_type"] for row in read_seed("day_types")}
    single = [row for row in read_seed("timetable_schedule_day_types") if row["iso_day_of_week"]]
    assert single
    for row in single:
        assert weekday_types[int(row["iso_day_of_week"])] == row["day_type"], row
    # One name never mixes a weekday-only row with an every-day row.
    names = {row["schedule_name"] for row in single}
    generic = {
        row["schedule_name"]
        for row in read_seed("timetable_schedule_day_types")
        if not row["iso_day_of_week"]
    }
    assert not names & generic


def test_fallback_frequencies_are_measurement_stations_inside_service_hours() -> None:
    """Every seed_line_frequencies row is a configured station of its line,
    covers whole hours inside line_operating_hours (a part hour is not
    measured, D33), and no two rows of a station and day type overlap."""
    stations = {
        (station.line_id, station.naptan_id)
        for station in load_stations(ROOT / "config" / "stations.yaml")
    }
    hours = {
        (row["line_id"], row["day_type"]): (
            int(row["service_start_minute"]),
            int(row["service_end_minute"]),
        )
        for row in read_seed("line_operating_hours")
    }
    covered: dict[tuple[str, str, str], set[int]] = {}
    for row in read_seed("seed_line_frequencies"):
        line_id, naptan_id, day_type = row["line_id"], row["naptan_id"], row["day_type"]
        assert (line_id, naptan_id) in stations, row
        start, end = hours[(line_id, day_type)]
        first, last = int(row["from_hour"]), int(row["to_hour"])
        assert first < last, row
        assert start <= first * 60, row
        assert last * 60 <= end, row
        assert int(row["trains_per_hour"]) > 0, row
        assert row["direction"] in {"inbound", "outbound", "both"}, row
        assert row["source"], row
        seen = covered.setdefault((line_id, naptan_id, row["direction"] + day_type), set())
        span = set(range(first, last))
        assert not seen.intersection(span), row
        seen.update(span)


def test_vehicle_id_lines_cover_every_line_once() -> None:
    """Every line has an explicit setting, so a line added to the config
    cannot fall silently to either linking rule (DECISIONS.md D27)."""
    rows = read_seed("seed_vehicle_id_lines")
    line_ids = [line.id for line in load_lines(ROOT / "config" / "lines.yaml")]
    assert sorted(row["line_id"] for row in rows) == sorted(line_ids)
    assert {row["link_by_vehicle_id"] for row in rows} <= {"true", "false"}
    assert all(row["decision_status"] for row in rows)


def test_vehicle_id_lines_are_the_settled_nine() -> None:
    """D27: Victoria plus the eight lines with stable ids; District is out."""
    rows = read_seed("seed_vehicle_id_lines")
    linked = {row["line_id"] for row in rows if row["link_by_vehicle_id"] == "true"}
    assert linked == {
        "bakerloo",
        "central",
        "circle",
        "hammersmith-city",
        "jubilee",
        "metropolitan",
        "northern",
        "piccadilly",
        "victoria",
    }


def test_measurement_stations_seed_matches_config() -> None:
    expected = [
        {
            "line_id": station.line_id,
            "naptan_id": station.naptan_id,
            "station_name": station.name,
            "role": station.role.value,
        }
        for station in load_stations(ROOT / "config" / "stations.yaml")
    ]
    assert read_seed("seed_measurement_stations") == expected


def test_measured_lines_cover_every_line_and_give_a_reason_when_off() -> None:
    rows = read_seed("seed_measured_lines")
    line_ids = [line.id for line in load_lines(ROOT / "config" / "lines.yaml")]
    assert sorted(row["line_id"] for row in rows) == sorted(line_ids)
    for row in rows:
        assert row["is_measured"] in {"true", "false"}
        assert (row["is_measured"] == "false") == bool(row["reason"]), row
