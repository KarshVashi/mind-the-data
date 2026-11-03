"""Lines in scope and measurement stations, loaded from ``config/``.

``lines.yaml`` lists every line the league covers; ``stations.yaml`` lists the
stations whose Arrivals board is polled for each line. Both files are small and
hand-maintained, so every rule that keeps them honest is checked at load time
rather than discovered as a gap in the data weeks later.
"""

import os
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# NaPTAN StopPoint ids that the Arrivals endpoint accepts: 940GZZ... for
# Underground, DLR and tram stations, 910G... for National Rail-style stations
# (Elizabeth line, Overground). Hub ids (HUB...) are rejected on purpose: a hub
# groups several stations, so one id would no longer mean one measured board.
NAPTAN_ID_PATTERN = r"^[0-9]{3}[A-Z0-9]+$"
MAX_STATIONS_PER_LINE = 5
DEFAULT_MIN_STATIONS = 3

LineId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z-]*$")]
NaptanId = Annotated[str, StringConstraints(pattern=NAPTAN_ID_PATTERN)]
HexColour = Annotated[str, StringConstraints(pattern=r"^#[0-9A-F]{6}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class ConfigError(ValueError):
    """A config file breaks one of the rules this module enforces."""


class Mode(StrEnum):
    TUBE = "tube"
    ELIZABETH_LINE = "elizabeth-line"
    DLR = "dlr"
    OVERGROUND = "overground"
    TRAM = "tram"


class StationRole(StrEnum):
    CORE = "core"
    BRANCH = "branch"
    TERMINUS = "terminus"


class Line(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: LineId
    name: NonEmpty
    mode: Mode
    colour: HexColour
    low_volume: bool
    # Fewer than three measurement stations is allowed only where the line
    # itself is that short (Waterloo & City, Liberty).
    min_stations: int = Field(default=DEFAULT_MIN_STATIONS, ge=1, le=MAX_STATIONS_PER_LINE)
    # Starting division; decided in M6, null until then.
    division_seed: int | None = Field(default=None, ge=1)


class Station(BaseModel):
    """One measurement station for one line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: LineId
    naptan_id: NaptanId
    name: NonEmpty
    role: StationRole
    reason: NonEmpty


def default_config_dir() -> Path:
    """``$CONFIG_DIR`` if set (Lambda bundles), else the repository's ``config/``."""
    override = os.environ.get("CONFIG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "config"


def _read_mapping(path: Path, key: str) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or key not in data:
        raise ConfigError(f"{path}: expected a mapping with top-level key {key!r}")
    return data[key]


def load_lines(path: Path) -> list[Line]:
    raw = _read_mapping(path, "lines")
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path}: 'lines' must be a non-empty list")
    lines = [Line.model_validate(item) for item in raw]
    duplicates = sorted(i for i, n in Counter(line.id for line in lines).items() if n > 1)
    if duplicates:
        raise ConfigError(f"{path}: duplicate line ids {duplicates}")
    return lines


def load_stations(path: Path) -> list[Station]:
    """Flatten ``lines: {line_id: [station, ...]}`` into one row per line and station."""
    raw = _read_mapping(path, "lines")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{path}: 'lines' must be a non-empty mapping of line id to stations")
    stations: list[Station] = []
    for line_id, entries in raw.items():
        if not isinstance(entries, list):
            raise ConfigError(f"{path}: stations for {line_id!r} must be a list")
        stations.extend(Station.model_validate({"line_id": line_id, **e}) for e in entries)

    pairs = Counter((s.line_id, s.naptan_id) for s in stations)
    duplicates = sorted(p for p, n in pairs.items() if n > 1)
    if duplicates:
        raise ConfigError(f"{path}: duplicate (line, naptan) pairs {duplicates}")

    names: dict[str, set[str]] = {}
    for s in stations:
        names.setdefault(s.naptan_id, set()).add(s.name)
    inconsistent = sorted(n for n, found in names.items() if len(found) > 1)
    if inconsistent:
        raise ConfigError(f"{path}: naptan ids with more than one name {inconsistent}")
    return stations


def validate_stations(lines: Sequence[Line], stations: Sequence[Station]) -> None:
    """Cross-file rules: known lines only, and every line measured within its limits."""
    known = {line.id: line for line in lines}
    unknown = sorted({s.line_id for s in stations} - known.keys())
    if unknown:
        raise ConfigError(f"stations reference lines not in lines.yaml: {unknown}")

    counts = Counter(s.line_id for s in stations)
    problems = [
        f"{line.id}: {counts[line.id]} (allowed {line.min_stations}..{MAX_STATIONS_PER_LINE})"
        for line in lines
        if not line.min_stations <= counts[line.id] <= MAX_STATIONS_PER_LINE
    ]
    if problems:
        raise ConfigError(f"measurement stations per line out of range: {problems}")


def distinct_station_ids(stations: Sequence[Station]) -> list[str]:
    """Sorted unique NaPTAN ids: one Arrivals call each per poll."""
    return sorted({s.naptan_id for s in stations})


def load_config(config_dir: Path | None = None) -> tuple[list[Line], list[Station]]:
    """Load and cross-check both files from ``config_dir`` (default: ``default_config_dir()``)."""
    directory = config_dir or default_config_dir()
    lines = load_lines(directory / "lines.yaml")
    stations = load_stations(directory / "stations.yaml")
    validate_stations(lines, stations)
    return lines, stations
