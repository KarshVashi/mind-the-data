"""NUMBAT and annual counts reduced to seeds (ADR-020), on hand-made sheets."""

import pytest

from mind_the_data.demand.numbat import (
    DayTypeInput,
    DemandFileError,
    LineBoarders,
    LinkLoad,
    StationCount,
    line_demand_rows,
    link_demand_rows,
    merge_split_stations,
    parse_line_boarders,
    parse_link_loads,
    parse_station_counts,
    quarter_hour_columns,
    station_demand_rows,
)
from mind_the_data.demand.xlsx import Cell


def qhr_labels() -> list[str]:
    """NUMBAT's 96 labels, 0500-0515 through 0445-0500."""
    labels = []
    for step in range(96):
        start = (5 * 60 + 15 * step) % (24 * 60)
        end = (start + 15) % (24 * 60)
        labels.append(f"{start // 60:02d}{start % 60:02d}-{end // 60:02d}{end % 60:02d}")
    return labels


HOURS = [int(label[:2]) for label in qhr_labels()]


def quarters(**by_label: float) -> list[float]:
    values = dict.fromkeys(qhr_labels(), 0.0)
    for label, value in by_label.items():
        values[label.removeprefix("q")] = value
    return list(values.values())


LINK_HEADER: list[Cell] = [
    "Link", "Line", "Dir", "Order", "From NLC", "From ASC", "From Station",
    "To NLC", "To ASC", "To Station", "Total", "Early", "AM Peak", "Midday",
    "PM Peak", "Evening", "Late", *qhr_labels(),
]  # fmt: skip


def link_row(line: str, total: float, values: list[float]) -> list[Cell]:
    return [
        "ELEu_BAK_NB>LAMu_BAK_NB@BAK", line, "NB", 1.0, 570.0, "ELEu",
        "Elephant & Castle LU", 628.0, "LAMu", "Lambeth North", total,
        None, None, None, None, None, None, *values,
    ]  # fmt: skip


def test_quarter_hours_map_to_clock_hours() -> None:
    columns = quarter_hour_columns(LINK_HEADER)
    assert columns[0] == (17, 5)
    # 00:30 is late in the traffic day that started at 05:00.
    assert columns[78] == (95, 0)
    assert len(columns) == 96


def test_parse_link_loads_checks_the_total() -> None:
    values = quarters(**{"q0800-0815": 30.0, "q0815-0830": 10.0})
    rows: list[list[Cell]] = [["Link Loads"], [], LINK_HEADER, link_row("Bakerloo", 40.0, values)]
    (link,) = parse_link_loads(rows)
    assert (link.numbat_line, link.from_asc, link.to_station) == (
        "Bakerloo",
        "ELEu",
        "Lambeth North",
    )
    assert link.total == 40.0

    rows[3] = link_row("Bakerloo", 55.0, values)
    with pytest.raises(DemandFileError, match="not the sum"):
        parse_link_loads(rows)


def test_an_unknown_line_stops_the_build() -> None:
    rows: list[list[Cell]] = [LINK_HEADER, link_row("Crossrail 2", 0.0, quarters())]
    with pytest.raises(DemandFileError, match="unknown NUMBAT line"):
        parse_link_loads(rows)


def test_parse_line_boarders_skips_the_network_total() -> None:
    header: list[Cell] = ["Mode", "Line Code", "Line Name", "Total", *["x"] * 6, *qhr_labels()]
    values = quarters(**{"q0700-0715": 5.0})
    rows: list[list[Cell]] = [
        header,
        ["LU", "VIC", "Victoria", 5.0, *[None] * 6, *values],
        [],
        ["TfL", "TfL", "TOTAL", 5.0, *[None] * 6, *values],
    ]
    assert parse_line_boarders(rows) == [LineBoarders("Victoria", tuple(values))]


def day_input(boarders: float, link_load: float, line: str = "Victoria") -> DayTypeInput:
    # Half the boarders at 08:00-08:15, half at 00:30-00:45 (Night Tube).
    board = tuple(quarters(**{"q0800-0815": boarders / 2, "q0030-0045": boarders / 2}))
    load = tuple(quarters(**{"q0800-0815": link_load}))
    link = LinkLoad("a>b", line, "NB", 1, "AAAu", "A", "BBBu", "B", load)
    return DayTypeInput(links=[link], boarders=[LineBoarders(line, board)], hours=HOURS)


def test_line_demand_mixes_monday_into_mon_thu() -> None:
    inputs = {
        "MON": day_input(boarders=40, link_load=100),
        "TWT": day_input(boarders=80, link_load=200),
        "FRI": day_input(boarders=120, link_load=0),
        "SAT": day_input(boarders=0, link_load=0),
        "SUN": day_input(boarders=8, link_load=4),
    }
    rows = {(r["service_day_type"], r["hour_of_day"]): r for r in line_demand_rows(inputs)}
    # mon_thu = 0.25 x Monday + 0.75 x Tuesday-to-Thursday: 0.25 x 20 + 0.75 x 40 = 35
    # boarders in each of 08:00 and 00:00; link loads 0.25 x 100 + 0.75 x 200 = 175.
    assert rows[("mon_thu", "8")]["boarders"] == "35.0"
    assert rows[("mon_thu", "8")]["link_load_sum"] == "175.0"
    assert rows[("mon_thu", "0")]["boarders"] == "35.0"
    assert rows[("fri", "0")]["boarders"] == "60.0"
    # Saturday has no demand at all, so no rows: no data is never zero.
    assert not any(day == "sat" for day, _ in rows)
    assert len([key for key in rows if key[0] == "sun"]) == 24


def test_line_demand_needs_every_day_type() -> None:
    with pytest.raises(DemandFileError, match="missing NUMBAT day types"):
        line_demand_rows({"TWT": day_input(1, 1)})


def test_link_demand_totals_per_day_type() -> None:
    inputs = {
        day: day_input(boarders=1, link_load=load)
        for day, load in {"MON": 100.0, "TWT": 200.0, "FRI": 50.0, "SAT": 0.0, "SUN": 10.0}.items()
    }
    (row,) = link_demand_rows(inputs)
    assert row["mon_thu_load"] == "175.0"
    assert row["fri_load"] == "50.0"
    assert row["sat_load"] == "0.0"
    assert row["am_peak_load"] == "175.0"


def test_station_counts_keep_an_uncounted_station_null() -> None:
    rows: list[list[Cell]] = [
        ["COUNTS"],
        [
            "Mode", "MNLC", "MASC", "Station", "Coverage", "Source", "Monday",
            "Midweek (Tue-Thu)", "Friday", "Saturday", "Sunday", "Monday",
            "Midweek (Tue-Thu)", "Friday", "Saturday", "Sunday", "Weekly",
            "12-week", "Annualised",
        ],
        [None, None, None, None, None, None, *["Entry"] * 5, *["Exit"] * 5, "En/Ex", "En/Ex", "En/Ex"],
        ["LU", 500.0, "ACTu", "Acton Town", "x", "taps", 1.0, 100.0, 1.0, 30.0, 20.0,
         1.0, 110.0, 1.0, 31.0, 21.0, 0.0, 0.0, 50000.0],
        ["LO", 501.0, "XXXr", "New Station", "x", "taps", "---", "---", "---", "---", "---",
         "---", "---", "---", "---", "---", "---", "---", "---"],
    ]  # fmt: skip
    uncounted, counted = station_demand_rows(parse_station_counts(rows))
    assert uncounted["station"] == "New Station"
    assert uncounted["weekday_entries_exits"] == ""
    assert uncounted["annual_entries_exits"] == ""
    assert counted["weekday_entries_exits"] == "210"
    assert counted["saturday_entries_exits"] == "61"
    assert counted["annual_entries_exits"] == "50000"


def test_a_line_listed_twice_is_an_error() -> None:
    twice = day_input(boarders=8, link_load=4)
    doubled = DayTypeInput(links=twice.links, boarders=twice.boarders * 2, hours=HOURS)
    inputs = {day: day_input(boarders=8, link_load=4) for day in ("MON", "FRI", "SAT", "SUN")}
    with pytest.raises(DemandFileError, match="listed twice"):
        line_demand_rows({**inputs, "TWT": doubled})


def test_a_line_missing_from_one_mixed_file_is_an_error() -> None:
    inputs = {day: day_input(boarders=8, link_load=4) for day in ("TWT", "FRI", "SAT", "SUN")}
    inputs["MON"] = day_input(boarders=8, link_load=4, line="Central")
    with pytest.raises(DemandFileError, match="which other files have"):
        line_demand_rows(inputs)


def test_a_station_split_over_two_rows_is_added() -> None:
    first = StationCount("EZL", "6888", "SNFr", "Shenfield", 32353.0, 20397.0, 51.0, 7084754.0)
    second = StationCount("EZL", "6888", "SNFr", "Shenfield", 97.0, 210.0, None, 1944713.0)
    (merged,) = merge_split_stations([first, second])
    assert merged.weekday_entries_exits == 32450
    assert merged.saturday_entries_exits == 20607
    assert merged.sunday_entries_exits is None
    assert merged.annual_entries_exits == 9029467
