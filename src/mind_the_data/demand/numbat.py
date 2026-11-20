"""Reduce NUMBAT and the annual counts to the seeds the models read (ADR-020).

NUMBAT gives typical autumn demand for five day types (Monday, Tuesday to
Thursday, Friday, Saturday, Sunday), each a traffic day from 05:00 to 04:59,
in quarter hours. The project's day types put Monday with Tuesday to
Thursday (`mon_thu`), so that day type is the 1:3 mix of NUMBAT's Monday and
its typical Tuesday-to-Thursday day.

A quarter hour is kept against its London clock hour and the NUMBAT traffic
day it belongs to (`service_day_type`): 00:30 in the Friday file is Saturday
morning's Night Tube, and is filed under `fri`, hour 0. The model maps each
minute to its service day the same way.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from mind_the_data.demand.xlsx import Cell

# NUMBAT file day types, and how each project day type is made of them.
NUMBAT_DAY_TYPES = ("MON", "TWT", "FRI", "SAT", "SUN")
DAY_TYPE_MIX: Mapping[str, Mapping[str, float]] = {
    "mon_thu": {"MON": 0.25, "TWT": 0.75},
    "fri": {"FRI": 1.0},
    "sat": {"SAT": 1.0},
    "sun": {"SUN": 1.0},
}

# Every line NUMBAT names. Trams are listed with zero demand in NUMBAT 2025,
# so they are known but produce no rows. An unknown name stops the build so a
# renamed line is never dropped quietly.
NUMBAT_LINES = (
    "Bakerloo",
    "Central",
    "DLR",
    "District",
    "Elizabeth Line",
    "H&C and Circle",
    "Jubilee",
    "LO Liberty",
    "LO Lioness",
    "LO Mildmay",
    "LO Suffragette",
    "LO Weaver",
    "LO Windrush",
    "London Trams",
    "Metropolitan",
    "Northern",
    "Piccadilly",
    "Victoria",
    "Waterloo & City",
)

_QHR = re.compile(r"(\d{2})(\d{2})-(\d{2})(\d{2})")
# A sheet's own total may differ from the sum of its quarter hours by
# rounding in the published file; anything beyond this is a parsing error.
TOTAL_TOLERANCE = 0.01


class DemandFileError(ValueError):
    """The workbook does not have the shape this module expects."""


@dataclass(frozen=True)
class LinkLoad:
    """One NUMBAT link: passengers on trains between two adjacent platforms."""

    link: str
    numbat_line: str
    direction: str
    order: int
    from_asc: str
    from_station: str
    to_asc: str
    to_station: str
    quarter_hours: tuple[float, ...]

    @property
    def total(self) -> float:
        return sum(self.quarter_hours)


@dataclass(frozen=True)
class LineBoarders:
    """Passengers boarding a NUMBAT line, by quarter hour."""

    numbat_line: str
    quarter_hours: tuple[float, ...]


@dataclass(frozen=True)
class StationCount:
    """A station's typical entries and exits from the annual counts."""

    mode: str
    mnlc: str
    masc: str
    station: str
    weekday_entries_exits: float | None
    saturday_entries_exits: float | None
    sunday_entries_exits: float | None
    annual_entries_exits: float | None


def _text(cell: Cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def _number(cell: Cell) -> float:
    if cell is None or cell == "":
        return 0.0
    if isinstance(cell, float):
        return cell
    try:
        return float(cell)
    except ValueError as exc:
        raise DemandFileError(f"not a number: {cell!r}") from exc


def _count(cell: Cell) -> float | None:
    """A count from the annual counts; "---" marks a station not counted."""
    if cell is None or _text(cell) in ("", "---"):
        return None
    return _number(cell)


def _whole(value: float | None) -> str:
    # Empty, not 0, so the seed loads a null for a station without a count.
    return "" if value is None else f"{value:.0f}"


def _header(rows: Sequence[Sequence[Cell]], first: str) -> int:
    for index, row in enumerate(rows):
        if row and _text(row[0]) == first:
            return index
    raise DemandFileError(f"no header row starting {first!r}")


def quarter_hour_columns(header: Sequence[Cell]) -> list[tuple[int, int]]:
    """(column, London clock hour) of each quarter-hour column, in order.

    NUMBAT labels them "0500-0515" to "0445-0500", 96 in a traffic day.
    """
    columns = []
    for index, cell in enumerate(header):
        match = _QHR.fullmatch(_text(cell))
        if match:
            columns.append((index, int(match.group(1))))
    if len(columns) != 96:
        raise DemandFileError(f"expected 96 quarter hours, found {len(columns)}")
    return columns


def _check_total(name: str, stated: float, quarter_hours: Sequence[float]) -> None:
    if abs(stated - sum(quarter_hours)) > TOTAL_TOLERANCE * max(1.0, abs(stated)):
        raise DemandFileError(
            f"{name}: total {stated:.1f} is not the sum of its quarter hours "
            f"{sum(quarter_hours):.1f}"
        )


def _known_line(name: str) -> str:
    if name not in NUMBAT_LINES:
        raise DemandFileError(f"unknown NUMBAT line {name!r}; map it in NUMBAT_LINES")
    return name


def parse_link_loads(rows: Sequence[Sequence[Cell]]) -> list[LinkLoad]:
    """The Link_Loads sheet: one row per link and direction."""
    start = _header(rows, "Link")
    header = rows[start]
    names = [_text(cell) for cell in header]
    try:
        col = {name: names.index(name) for name in ("Line", "Dir", "Order", "Total")}
        col["from_asc"] = names.index("From ASC")
        col["from_station"] = names.index("From Station")
        col["to_asc"] = names.index("To ASC")
        col["to_station"] = names.index("To Station")
    except ValueError as exc:
        raise DemandFileError(f"Link_Loads header is missing a column: {exc}") from exc
    quarters = quarter_hour_columns(header)
    links = []
    for row in rows[start + 1 :]:
        if not row or not _text(row[0]):
            continue
        padded = list(row) + [None] * (len(header) - len(row))
        values = tuple(_number(padded[index]) for index, _ in quarters)
        link = LinkLoad(
            link=_text(padded[0]),
            numbat_line=_known_line(_text(padded[col["Line"]])),
            direction=_text(padded[col["Dir"]]),
            order=int(_number(padded[col["Order"]])),
            from_asc=_text(padded[col["from_asc"]]),
            from_station=_text(padded[col["from_station"]]),
            to_asc=_text(padded[col["to_asc"]]),
            to_station=_text(padded[col["to_station"]]),
            quarter_hours=values,
        )
        _check_total(link.link, _number(padded[col["Total"]]), values)
        links.append(link)
    if not links:
        raise DemandFileError("Link_Loads has no links")
    return links


def parse_line_boarders(rows: Sequence[Sequence[Cell]]) -> list[LineBoarders]:
    """The Line_Boarders sheet: one row per line, then a network total."""
    start = _header(rows, "Mode")
    header = rows[start]
    names = [_text(cell) for cell in header]
    if "Line Name" not in names or "Total" not in names:
        raise DemandFileError("Line_Boarders header has no Line Name or Total")
    name_col, total_col = names.index("Line Name"), names.index("Total")
    quarters = quarter_hour_columns(header)
    lines = []
    for row in rows[start + 1 :]:
        padded = list(row) + [None] * (len(header) - len(row))
        name = _text(padded[name_col])
        if not name or name == "TOTAL":
            continue
        values = tuple(_number(padded[index]) for index, _ in quarters)
        _check_total(name, _number(padded[total_col]), values)
        lines.append(LineBoarders(numbat_line=_known_line(name), quarter_hours=values))
    return lines


def hourly(quarter_hours: Sequence[float], hours: Sequence[int]) -> dict[int, float]:
    """Sum quarter hours into London clock hours (0 to 23)."""
    totals = {hour: 0.0 for hour in range(24)}
    for value, hour in zip(quarter_hours, hours, strict=True):
        totals[hour] += value
    return totals


def _mix(by_file: Mapping[str, Mapping[str, float]], mix: Mapping[str, float]) -> dict[str, float]:
    keys: set[str] = set()
    for file_type in mix:
        keys |= set(by_file.get(file_type, {}))
    return {
        key: sum(
            weight * by_file.get(file_type, {}).get(key, 0.0) for file_type, weight in mix.items()
        )
        for key in sorted(keys)
    }


@dataclass(frozen=True)
class DayTypeInput:
    """What one NUMBAT file (one day type) contributes."""

    links: list[LinkLoad]
    boarders: list[LineBoarders]
    hours: list[int]


def _check_day_types(inputs: Mapping[str, DayTypeInput]) -> None:
    missing = set(NUMBAT_DAY_TYPES) - set(inputs)
    if missing:
        raise DemandFileError(f"missing NUMBAT day types: {sorted(missing)}")


def line_demand_rows(inputs: Mapping[str, DayTypeInput]) -> list[dict[str, str]]:
    """seed_line_demand: boarders and summed link loads per line, day type, hour.

    `link_load_sum` adds every link of the line in both directions, so it is
    passenger-links per hour: a passenger riding five stops counts five times.
    `boarders` counts each boarding of the line once. Lines with no demand in
    the day type (trams) produce no rows, so the model can tell no data from
    zero.
    """
    _check_day_types(inputs)
    boarders: dict[str, dict[str, float]] = {}
    loads: dict[str, dict[str, float]] = {}
    for file_type, item in inputs.items():
        per_line_hour: dict[str, float] = {}
        for boarded in item.boarders:
            for hour, value in hourly(boarded.quarter_hours, item.hours).items():
                per_line_hour[f"{boarded.numbat_line}|{hour}"] = value
        boarders[file_type] = per_line_hour
        link_hour: dict[str, float] = {}
        for link in item.links:
            for hour, value in hourly(link.quarter_hours, item.hours).items():
                key = f"{link.numbat_line}|{hour}"
                link_hour[key] = link_hour.get(key, 0.0) + value
        loads[file_type] = link_hour

    rows: list[dict[str, str]] = []
    for day_type, mix in DAY_TYPE_MIX.items():
        mixed_boarders = _mix(boarders, mix)
        mixed_loads = _mix(loads, mix)
        line_totals: dict[str, float] = {}
        for key, value in mixed_boarders.items():
            name = key.split("|")[0]
            line_totals[name] = line_totals.get(name, 0.0) + value
        for key in sorted(set(mixed_boarders) | set(mixed_loads), key=_line_hour_order):
            name, hour_label = key.split("|")
            if line_totals.get(name, 0.0) <= 0:
                continue
            rows.append(
                {
                    "numbat_line": name,
                    "service_day_type": day_type,
                    "hour_of_day": hour_label,
                    "boarders": f"{mixed_boarders.get(key, 0.0):.1f}",
                    "link_load_sum": f"{mixed_loads.get(key, 0.0):.1f}",
                }
            )
    return rows


def _line_hour_order(key: str) -> tuple[str, int]:
    line, hour = key.split("|")
    # The NUMBAT traffic day starts at 05:00.
    return line, (int(hour) - 5) % 24


def link_demand_rows(inputs: Mapping[str, DayTypeInput]) -> list[dict[str, str]]:
    """seed_link_demand: each link's typical daily load per project day type.

    `am_peak_load` is 07:00 to 10:00 on a Monday-to-Thursday day.
    """
    _check_day_types(inputs)
    by_link: dict[str, LinkLoad] = {}
    totals: dict[str, dict[str, float]] = {}
    am_peak: dict[str, dict[str, float]] = {}
    for file_type, item in inputs.items():
        totals[file_type] = {}
        am_peak[file_type] = {}
        for link in item.links:
            key = f"{link.numbat_line}|{link.link}"
            by_link.setdefault(key, link)
            totals[file_type][key] = link.total
            per_hour = hourly(link.quarter_hours, item.hours)
            am_peak[file_type][key] = per_hour[7] + per_hour[8] + per_hour[9]
    mixed = {day_type: _mix(totals, mix) for day_type, mix in DAY_TYPE_MIX.items()}
    mixed_am = _mix(am_peak, DAY_TYPE_MIX["mon_thu"])
    rows = []
    for key in sorted(
        by_link, key=lambda k: (by_link[k].numbat_line, by_link[k].direction, by_link[k].order)
    ):
        link = by_link[key]
        if all(mixed[day_type].get(key, 0.0) <= 0 for day_type in DAY_TYPE_MIX):
            continue
        rows.append(
            {
                "link_id": link.link,
                "numbat_line": link.numbat_line,
                "direction": link.direction,
                "link_order": str(link.order),
                "from_asc": link.from_asc,
                "from_station": link.from_station,
                "to_asc": link.to_asc,
                "to_station": link.to_station,
                "mon_thu_load": f"{mixed['mon_thu'].get(key, 0.0):.1f}",
                "fri_load": f"{mixed['fri'].get(key, 0.0):.1f}",
                "sat_load": f"{mixed['sat'].get(key, 0.0):.1f}",
                "sun_load": f"{mixed['sun'].get(key, 0.0):.1f}",
                "am_peak_load": f"{mixed_am.get(key, 0.0):.1f}",
            }
        )
    return rows


def parse_station_counts(rows: Sequence[Sequence[Cell]]) -> list[StationCount]:
    """The annual counts sheet: typical entries and exits per station.

    The header row names the day types; the row below says Entry or Exit.
    Weekday is the Tuesday-to-Thursday typical day ("Midweek").
    """
    start = _header(rows, "Mode")
    days = [_text(cell) for cell in rows[start]]
    kinds = [_text(cell) for cell in rows[start + 1]]

    def column(day: str, kind: str) -> int:
        for index, (name, which) in enumerate(zip(days, kinds, strict=False)):
            if name.startswith(day) and which == kind:
                return index
        raise DemandFileError(f"annual counts have no {day} {kind} column")

    cols = {
        "weekday": (column("Midweek", "Entry"), column("Midweek", "Exit")),
        "saturday": (column("Saturday", "Entry"), column("Saturday", "Exit")),
        "sunday": (column("Sunday", "Entry"), column("Sunday", "Exit")),
    }
    annual = days.index("Annualised")
    counts = []
    for row in rows[start + 2 :]:
        padded = list(row) + [None] * (len(days) - len(row))
        if not _text(padded[0]) or not _text(padded[3]):
            continue

        def both(pair: tuple[int, int], cells: list[Cell] = padded) -> float | None:
            entries, exits = _count(cells[pair[0]]), _count(cells[pair[1]])
            return None if entries is None or exits is None else entries + exits

        counts.append(
            StationCount(
                mode=_text(padded[0]),
                mnlc=_text(padded[1]),
                masc=_text(padded[2]),
                station=_text(padded[3]),
                weekday_entries_exits=both(cols["weekday"]),
                saturday_entries_exits=both(cols["saturday"]),
                sunday_entries_exits=both(cols["sunday"]),
                annual_entries_exits=_count(padded[annual]),
            )
        )
    if not counts:
        raise DemandFileError("annual counts have no stations")
    return counts


def station_demand_rows(counts: Iterable[StationCount]) -> list[dict[str, str]]:
    """seed_station_demand: typical daily entries plus exits per station."""
    return [
        {
            "mode": count.mode,
            "mnlc": count.mnlc,
            "masc": count.masc,
            "station": count.station,
            "weekday_entries_exits": _whole(count.weekday_entries_exits),
            "saturday_entries_exits": _whole(count.saturday_entries_exits),
            "sunday_entries_exits": _whole(count.sunday_entries_exits),
            "annual_entries_exits": _whole(count.annual_entries_exits),
        }
        for count in sorted(counts, key=lambda c: (c.mode, c.station, c.mnlc))
    ]
