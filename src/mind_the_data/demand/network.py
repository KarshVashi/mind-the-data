"""Which links carry the most passengers, and which of them were disrupted.

A deliberately simple method (ADR-020, "Critical links"):

- A link is a pair of adjacent stations on one NUMBAT line. Its typical load
  is the passengers over it on a typical day, both directions added.
  Closing it affects roughly those passengers; nobody is rerouted.
- The only rerouting idea is a count of stops: the fewest stops between the
  link's two stations on the rest of the network without that link. One
  means another line runs between the same two stations; None means the
  network splits. Stops are not minutes, and interchanges are free.
- A disrupted minute covers a link when TfL's reason text puts the link in a
  "between X and Y" section of the line (along the line's shortest path
  from X to Y), or when the text names no section and the status is not a
  partial one. "Minor delays on the rest of the line" gives the links
  outside the section weight 1; "severe delays", 3.

Station names in TfL's text are matched to NUMBAT's by a normalised name;
a name that does not match leaves its section unknown, and those minutes are
counted apart, never as covering the link.
"""

import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

DAY_TYPES = ("mon_thu", "fri", "sat", "sun")
# Statuses that by definition cover only part of a line. Without a section
# in the text they cannot be placed.
PARTIAL_STATUSES = frozenset({"Part Suspended", "Part Closure"})
REST_OF_LINE = re.compile(r"\b(minor|severe) delays (?:on )?the rest of the line")
REST_OF_LINE_WEIGHTS = {"minor": 1.0, "severe": 3.0}
# A reason can give each section its own status: "No service between A and
# B. SEVERE DELAYS between B and C". The weights follow severity_weights.
SECTION_PHRASE = re.compile(r"\b(no service|severe delays|minor delays|reduced service)\b")
SECTION_WEIGHTS = {"severe delays": 3.0, "minor delays": 1.0, "reduced service": 1.5}

_SUFFIXES = re.compile(r"\b(lu|nr|lo|el|dlr|tfl|underground station|rail station|station)\b")


# TfL's text uses short forms NUMBAT does not; applied to normalised text.
TEXT_ALIASES = (
    ("heathrow airport", "heathrow terminals"),
    ("harrow on hill", "harrow on the hill"),
)


def normalise(name: str) -> str:
    """A station name reduced for matching TfL's text against NUMBAT's names."""
    text = name.lower().replace("&", " and ").replace("’", "'")
    text = re.sub(r"\(.*?\)", " ", text)
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9/]+", " ", text)
    text = text.replace("/", " / ")
    text = _SUFFIXES.sub(" ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class Link:
    """Two adjacent stations on one NUMBAT line, both directions together."""

    numbat_line: str
    a: str
    b: str
    a_name: str
    b_name: str
    loads: Mapping[str, float]
    am_peak_load: float

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.numbat_line, self.a, self.b)


def links_from_seed(rows: Iterable[Mapping[str, str]]) -> dict[tuple[str, str, str], Link]:
    """Fold seed_link_demand's directional rows into undirected links."""
    names: dict[tuple[str, str, str], tuple[str, str]] = {}
    loads: dict[tuple[str, str, str], dict[str, float]] = {}
    am_peak: dict[tuple[str, str, str], float] = {}
    for row in rows:
        (a, a_name), (b, b_name) = sorted(
            [(row["from_asc"], row["from_station"]), (row["to_asc"], row["to_station"])]
        )
        if a == b:
            continue
        key = (row["numbat_line"], a, b)
        names.setdefault(key, (a_name, b_name))
        day_loads = loads.setdefault(key, dict.fromkeys(DAY_TYPES, 0.0))
        for day in DAY_TYPES:
            day_loads[day] += float(row[f"{day}_load"] or 0)
        am_peak[key] = am_peak.get(key, 0.0) + float(row["am_peak_load"] or 0)
    return {
        key: Link(
            numbat_line=key[0],
            a=key[1],
            b=key[2],
            a_name=names[key][0],
            b_name=names[key][1],
            loads=loads[key],
            am_peak_load=am_peak[key],
        )
        for key in names
    }


def name_index(links: Iterable[Link]) -> dict[str, dict[str, str]]:
    """Per NUMBAT line, normalised station name to station code.

    A name's leading words (two or more) are also accepted when no other
    station on the line starts with them, so TfL's "King's Cross" finds
    "King's Cross St. Pancras" and "Heathrow Terminals" finds "Heathrow
    Terminals 123".
    """
    full: dict[str, dict[str, str]] = {}
    for link in links:
        line_names = full.setdefault(link.numbat_line, {})
        line_names.setdefault(normalise(link.a_name), link.a)
        line_names.setdefault(normalise(link.b_name), link.b)
    index: dict[str, dict[str, str]] = {}
    for line, line_names in full.items():
        prefixes: dict[str, set[str]] = {}
        for name, code in line_names.items():
            words = name.split()
            for size in range(2, len(words)):
                prefixes.setdefault(" ".join(words[:size]), set()).add(code)
        index[line] = dict(line_names)
        for prefix, codes in prefixes.items():
            if len(codes) == 1 and prefix not in line_names:
                index[line][prefix] = next(iter(codes))
    return index


Graph = dict[str, dict[str, int]]


def graph(links: Iterable[Link]) -> Graph:
    """Station adjacency, with the number of lines joining each pair."""
    adjacency: Graph = {}
    for link in links:
        for x, y in ((link.a, link.b), (link.b, link.a)):
            neighbours = adjacency.setdefault(x, {})
            neighbours[y] = neighbours.get(y, 0) + 1
    return adjacency


def stops_without(adjacency: Graph, link: Link) -> int | None:
    """Fewest stops from one end of `link` to the other without it."""
    if link.a not in adjacency:
        return None
    seen = {link.a: 0}
    queue = deque([link.a])
    while queue:
        here = queue.popleft()
        for there, lines in adjacency.get(here, {}).items():
            removed = {here, there} == {link.a, link.b}
            if removed and lines <= 1:
                continue
            if there not in seen:
                seen[there] = seen[here] + 1
                if there == link.b:
                    return seen[there]
                queue.append(there)
    return None


def path_pairs(adjacency: Graph, start: str, goal: str) -> set[frozenset[str]]:
    """Station pairs on one shortest path from start to goal (empty if none)."""
    previous: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        if here == goal:
            break
        for there in sorted(adjacency.get(here, {})):
            if there not in previous:
                previous[there] = here
                queue.append(there)
    if goal not in previous:
        return set()
    pairs = set()
    node = goal
    while (back := previous[node]) is not None:
        pairs.add(frozenset((node, back)))
        node = back
    return pairs


@dataclass(frozen=True)
class Section:
    start: str
    ends: tuple[str, ...]
    # The status the text gives this section ("severe delays between ..."),
    # or None when it names none; then the minute's own status applies.
    phrase: str | None = None


def mentions(text: str, names: Mapping[str, str]) -> list[tuple[int, int, str]]:
    """(start, end, station code) of each station name in normalised text.

    Longer names win where names overlap ("Ealing Broadway" over "Ealing").
    """
    found: list[tuple[int, int, str]] = []
    taken: set[int] = set()
    for name in sorted(names, key=len, reverse=True):
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text):
            span = set(range(match.start(), match.end()))
            if span & taken:
                continue
            taken |= span
            found.append((match.start(), match.end(), names[name]))
    return sorted(found)


def sections(reason: str, names: Mapping[str, str]) -> tuple[list[Section], bool]:
    """The "between X and Y[ / Z]" sections in a reason, and whether any failed.

    Returns the sections it could place and True when the text has a
    "between" whose stations it could not match.
    """
    text = normalise(reason)
    for short, long in TEXT_ALIASES:
        text = re.sub(rf"\b{short}\b", long, text)
    found = mentions(text, names)
    placed: list[Section] = []
    unmatched = False
    clause_start = 0
    for between in re.finditer(r"\bbetween\b", text):
        phrases = SECTION_PHRASE.findall(text[clause_start : between.start()])
        phrase = phrases[-1] if phrases else None
        clause_start = between.end()
        after = [m for m in found if m[0] >= between.end()]
        if not after or text[between.end() : after[0][0]].strip():
            unmatched = True
            continue
        start = after[0]
        ends: list[str] = []
        cursor = start
        for nxt in after[1:]:
            gap = text[cursor[1] : nxt[0]].strip()
            if (not ends and gap in ("and", "to")) or (ends and gap in ("/", "or")):
                ends.append(nxt[2])
                cursor = nxt
            else:
                break
        if ends:
            placed.append(Section(start=start[2], ends=tuple(ends), phrase=phrase))
        else:
            unmatched = True
    return placed, unmatched


def section_weight(section: Section, severity_weight: float) -> float:
    """A section's weight: its own phrase's, capped at the minute's status."""
    if section.phrase is None or section.phrase not in SECTION_WEIGHTS:
        return severity_weight
    return min(SECTION_WEIGHTS[section.phrase], severity_weight)


def rest_of_line_weight(reason: str) -> float:
    """Severity weight TfL gives the line outside the named sections, if any."""
    match = REST_OF_LINE.search(reason.lower())
    return REST_OF_LINE_WEIGHTS[match.group(1)] if match else 0.0


@dataclass(frozen=True)
class Coverage:
    """How one disrupted status bears on one link."""

    weight: float
    placed: bool


def coverage(
    link: Link,
    reason: str | None,
    status: str,
    severity_weight: float,
    line_adjacency: Graph,
    names: Mapping[str, str],
) -> Coverage:
    """The weight a disrupted minute puts on `link`, and whether it was placed."""
    found, unmatched = sections(reason or "", names)
    if not found:
        if unmatched or status in PARTIAL_STATUSES:
            return Coverage(weight=0.0, placed=False)
        return Coverage(weight=severity_weight, placed=True)
    pair = frozenset((link.a, link.b))
    weights = [
        section_weight(section, severity_weight)
        for section in found
        if any(pair in path_pairs(line_adjacency, section.start, end) for end in section.ends)
    ]
    if weights:
        return Coverage(weight=max(weights), placed=True)
    return Coverage(weight=rest_of_line_weight(reason or ""), placed=True)


@dataclass(frozen=True)
class StatusSpell:
    """Disrupted minutes of one TfL line sharing a status, reason and hour."""

    line_id: str
    status: str
    reason: str | None
    severity_weight: float
    service_day_type: str
    hour_of_day: int
    minutes: int


@dataclass
class Exposure:
    covered_minutes: int = 0
    heavy_minutes: int = 0
    unplaced_minutes: int = 0
    passenger_weighted: float = 0.0
    reasons: set[str] = field(default_factory=set)


def hour_shares(rows: Iterable[Mapping[str, str]]) -> dict[tuple[str, str, int], float]:
    """Each hour's share of a NUMBAT line's day of link loads (seed_line_demand)."""
    totals: dict[tuple[str, str], float] = {}
    values: dict[tuple[str, str, int], float] = {}
    for row in rows:
        key = (row["numbat_line"], row["service_day_type"])
        value = float(row["link_load_sum"])
        totals[key] = totals.get(key, 0.0) + value
        values[(*key, int(row["hour_of_day"]))] = value
    return {
        key: (value / totals[key[:2]] if totals[key[:2]] > 0 else 0.0)
        for key, value in values.items()
    }


def exposure(
    link: Link,
    spells: Sequence[StatusSpell],
    line_shares: Mapping[str, float],
    shares: Mapping[tuple[str, str, int], float],
    line_adjacency: Graph,
    names: Mapping[str, str],
) -> Exposure:
    """Disrupted minutes on `link` and severity x typical passengers over them.

    `line_shares` maps each TfL line id serving the NUMBAT line to its share
    of the demand (seed_demand_line_map), so Circle and Hammersmith & City
    minutes each carry half the shared link's passengers.
    """
    result = Exposure()
    for spell in spells:
        share = line_shares.get(spell.line_id)
        if share is None or spell.severity_weight <= 0:
            continue
        cover = coverage(
            link, spell.reason, spell.status, spell.severity_weight, line_adjacency, names
        )
        if not cover.placed:
            result.unplaced_minutes += spell.minutes
            continue
        if cover.weight <= 0:
            continue
        result.covered_minutes += spell.minutes
        if cover.weight >= 5:
            result.heavy_minutes += spell.minutes
        per_minute = (
            link.loads.get(spell.service_day_type, 0.0)
            * shares.get((link.numbat_line, spell.service_day_type, spell.hour_of_day), 0.0)
            / 60
        )
        result.passenger_weighted += cover.weight * per_minute * spell.minutes * share
        if spell.reason:
            result.reasons.add(spell.reason)
    return result
