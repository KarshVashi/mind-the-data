"""journeys/index.json and journeys/{line}/{direction}/{origin}.json (Should I Run, ADR-018).

Written only when JOURNEYS_ENABLED=true (off by default, D76). One file per
line, direction and origin station: every destination a train from there
reaches (rpt_station_reach), with the board destinations that call at it,
and the weekday 15-minute planning slots (rpt_journey_slots).

A slot's figures are published only when its status is `ok` (the gate
passed and enough runs over enough weeks). Otherwise the slot is written
with status `not_enough_data` and null figures: never a guess, and never
read as a good slot. The page computes leave-by times from these figures;
nothing here knows anyone's journey.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mind_the_data.ops.athena import run_query
from mind_the_data.publish.documents import Stamp, _round, _utc
from mind_the_data.publish.marts import (
    Cells,
    _bool,
    _date,
    _int,
    _opt,
    _opt_float,
    _opt_int,
    _req,
    load_recording,
)
from mind_the_data.publish.schemas import SCHEMA_VERSION

if TYPE_CHECKING:
    from mypy_boto3_athena import AthenaClient

JOURNEYS_ENABLED_VAR = "JOURNEYS_ENABLED"
JOURNEY_INDEX_PATH = "journeys/index.json"
JOURNEY_PATH_TEMPLATE = "journeys/{line_id}/{direction}/{naptan_id}.json"
STATUS_OK = "ok"
# Both tables hold every pair of stations on a line; read in full.
JOURNEY_MAX_ROWS = 2_000_000

SLOT_COLUMNS = (
    "line_id, direction, origin_naptan_id, destination_naptan_id, day_class, "
    "slot_start_minute, slot_end_minute, days_counted, passes_gate, runs, "
    "weeks_with_runs, run_p50_seconds, run_p90_seconds, trains_seen_per_day, "
    "scheduled_per_day, typical_wait_seconds, worst_wait_p90_seconds, status, "
    "first_london_date, last_london_date"
)
REACH_COLUMNS = (
    "line_id, direction, origin_naptan_id, origin_name, destination_naptan_id, "
    "destination_name, train_destination_naptan_id, train_destination_name, "
    "stops_to_destination, is_ambiguous"
)


@dataclass(frozen=True)
class JourneySlotRow:
    line_id: str
    direction: str
    origin_naptan_id: str
    destination_naptan_id: str
    day_class: str
    slot_start_minute: int
    slot_end_minute: int
    days_counted: int
    passes_gate: bool
    runs: int
    weeks_with_runs: int
    run_p50_seconds: int | None
    run_p90_seconds: int | None
    trains_seen_per_day: float | None
    scheduled_per_day: float | None
    typical_wait_seconds: float | None
    worst_wait_p90_seconds: int | None
    status: str
    first_london_date: date | None
    last_london_date: date | None


@dataclass(frozen=True)
class ReachRow:
    line_id: str
    direction: str
    origin_naptan_id: str
    origin_name: str | None
    destination_naptan_id: str
    destination_name: str | None
    train_destination_naptan_id: str
    train_destination_name: str | None
    stops_to_destination: int
    is_ambiguous: bool


@dataclass(frozen=True)
class JourneyRows:
    slots: Sequence[JourneySlotRow]
    reach: Sequence[ReachRow]


def parse_slot(cells: Cells) -> JourneySlotRow:
    first = _opt(cells, "first_london_date")
    last = _opt(cells, "last_london_date")
    return JourneySlotRow(
        line_id=_req(cells, "line_id"),
        direction=_req(cells, "direction"),
        origin_naptan_id=_req(cells, "origin_naptan_id"),
        destination_naptan_id=_req(cells, "destination_naptan_id"),
        day_class=_req(cells, "day_class"),
        slot_start_minute=_int(cells, "slot_start_minute"),
        slot_end_minute=_int(cells, "slot_end_minute"),
        days_counted=_int(cells, "days_counted"),
        passes_gate=_bool(cells, "passes_gate"),
        runs=_int(cells, "runs"),
        weeks_with_runs=_int(cells, "weeks_with_runs"),
        run_p50_seconds=_opt_int(cells, "run_p50_seconds"),
        run_p90_seconds=_opt_int(cells, "run_p90_seconds"),
        trains_seen_per_day=_opt_float(cells, "trains_seen_per_day"),
        scheduled_per_day=_opt_float(cells, "scheduled_per_day"),
        typical_wait_seconds=_opt_float(cells, "typical_wait_seconds"),
        worst_wait_p90_seconds=_opt_int(cells, "worst_wait_p90_seconds"),
        status=_req(cells, "status"),
        first_london_date=None if first is None else _date(cells, "first_london_date"),
        last_london_date=None if last is None else _date(cells, "last_london_date"),
    )


def parse_reach(cells: Cells) -> ReachRow:
    return ReachRow(
        line_id=_req(cells, "line_id"),
        direction=_req(cells, "direction"),
        origin_naptan_id=_req(cells, "origin_naptan_id"),
        origin_name=_opt(cells, "origin_name"),
        destination_naptan_id=_req(cells, "destination_naptan_id"),
        destination_name=_opt(cells, "destination_name"),
        train_destination_naptan_id=_req(cells, "train_destination_naptan_id"),
        train_destination_name=_opt(cells, "train_destination_name"),
        stops_to_destination=_int(cells, "stops_to_destination"),
        is_ambiguous=_bool(cells, "is_ambiguous"),
    )


def journey_queries(database: str) -> dict[str, str]:
    return {
        "journey_slots": (
            f"select {SLOT_COLUMNS} from {database}.rpt_journey_slots "
            "order by line_id, direction, origin_naptan_id, destination_naptan_id, "
            "slot_start_minute"
        ),
        "station_reach": (
            f"select {REACH_COLUMNS} from {database}.rpt_station_reach "
            "order by line_id, direction, origin_naptan_id, destination_naptan_id, "
            "train_destination_naptan_id"
        ),
    }


def fetch_journey_rows(athena: "AthenaClient", *, workgroup: str, database: str) -> JourneyRows:
    """Read both journey marts through Athena (single SELECTs only)."""
    cells: dict[str, list[dict[str, str | None]]] = {}
    for stem, sql in journey_queries(database).items():
        result = run_query(
            athena, sql, workgroup=workgroup, database=database, max_rows=JOURNEY_MAX_ROWS
        )
        if len(result.rows) >= JOURNEY_MAX_ROWS:
            raise RuntimeError(f"{stem}: result hit max_rows; refusing to truncate")
        cells[stem] = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
    return JourneyRows(
        slots=[parse_slot(c) for c in cells["journey_slots"]],
        reach=[parse_reach(c) for c in cells["station_reach"]],
    )


def load_recorded_journeys(directory: Path) -> JourneyRows | None:
    """Saved query results (`journey_slots.json`, `station_reach.json`), if both exist."""
    slots, reach = directory / "journey_slots.json", directory / "station_reach.json"
    if not (slots.exists() and reach.exists()):
        return None
    return JourneyRows(
        slots=[parse_slot(c) for c in load_recording(slots).cells()],
        reach=[parse_reach(c) for c in load_recording(reach).cells()],
    )


def journey_path(line_id: str, direction: str, naptan_id: str) -> str:
    return JOURNEY_PATH_TEMPLATE.format(line_id=line_id, direction=direction, naptan_id=naptan_id)


def clock(minute_of_day: int) -> str:
    """London wall-clock HH:MM for a minute of the day."""
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def _slot(row: JourneySlotRow) -> dict[str, Any]:
    ok = row.status == STATUS_OK
    return {
        "day_class": row.day_class,
        "start": clock(row.slot_start_minute),
        "end": clock(row.slot_end_minute),
        "status": row.status,
        "days_counted": row.days_counted,
        "passes_gate": row.passes_gate,
        "runs": row.runs,
        "weeks_with_runs": row.weeks_with_runs,
        # Figures only for a slot with enough gated history (D76).
        "run_seconds": {"p50": row.run_p50_seconds, "p90": row.run_p90_seconds}
        if ok and row.run_p50_seconds is not None and row.run_p90_seconds is not None
        else None,
        "wait_seconds": {
            "typical": _round(row.typical_wait_seconds, 0),
            "worst_p90": row.worst_wait_p90_seconds,
        }
        if ok and row.typical_wait_seconds is not None and row.worst_wait_p90_seconds is not None
        else None,
        "trains_seen_per_day": _round(row.trains_seen_per_day, 1) if ok else None,
        "scheduled_per_day": _round(row.scheduled_per_day, 1) if ok else None,
    }


def _window(slots: Sequence[JourneySlotRow]) -> dict[str, Any]:
    firsts = [s.first_london_date for s in slots if s.first_london_date is not None]
    lasts = [s.last_london_date for s in slots if s.last_london_date is not None]
    return {
        "start": min(firsts).isoformat() if firsts else None,
        "end": max(lasts).isoformat() if lasts else None,
        "timezone": "Europe/London",
    }


def _head(stamp: Stamp, slots: Sequence[JourneySlotRow]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc(stamp.generated_at),
        "git_sha": stamp.git_sha,
        "data_window": _window(slots),
        "attribution": "Powered by TfL Open Data",
        "disclaimer": "Estimates, not guarantees.",
    }


Key = tuple[str, str, str]


def journey_documents(rows: JourneyRows, stamp: Stamp) -> list[tuple[str, dict[str, Any], str]]:
    """(path, document, schema name) for the index and every origin file."""
    reach: dict[Key, list[ReachRow]] = defaultdict(list)
    for r in rows.reach:
        reach[(r.line_id, r.direction, r.origin_naptan_id)].append(r)
    slots: dict[Key, list[JourneySlotRow]] = defaultdict(list)
    for s in rows.slots:
        slots[(s.line_id, s.direction, s.origin_naptan_id)].append(s)

    names: dict[str, str] = {}
    for r in rows.reach:
        for naptan_id, name in (
            (r.origin_naptan_id, r.origin_name),
            (r.destination_naptan_id, r.destination_name),
            (r.train_destination_naptan_id, r.train_destination_name),
        ):
            if name:
                names[naptan_id] = name

    documents: list[tuple[str, dict[str, Any], str]] = []
    index_entries = []
    for key in sorted(reach):
        line_id, direction, origin = key
        by_destination: dict[str, list[ReachRow]] = defaultdict(list)
        for r in reach[key]:
            by_destination[r.destination_naptan_id].append(r)
        slots_by_destination: dict[str, list[JourneySlotRow]] = defaultdict(list)
        for s in slots[key]:
            slots_by_destination[s.destination_naptan_id].append(s)
        destinations = []
        for destination in sorted(
            by_destination, key=lambda d: (by_destination[d][0].stops_to_destination, d)
        ):
            trains = sorted(
                by_destination[destination], key=lambda r: r.train_destination_naptan_id
            )
            destinations.append(
                {
                    "naptan_id": destination,
                    "name": names.get(destination),
                    "stops": trains[0].stops_to_destination,
                    "train_destinations": [
                        {
                            "naptan_id": t.train_destination_naptan_id,
                            "name": names.get(t.train_destination_naptan_id),
                            "is_ambiguous": t.is_ambiguous,
                        }
                        for t in trains
                    ],
                    "slots": [
                        _slot(s)
                        for s in sorted(
                            slots_by_destination[destination],
                            key=lambda s: (s.day_class, s.slot_start_minute),
                        )
                    ],
                }
            )
        path = journey_path(line_id, direction, origin)
        documents.append(
            (
                path,
                {
                    **_head(stamp, slots[key]),
                    "line_id": line_id,
                    "direction": direction,
                    "origin": {"naptan_id": origin, "name": names.get(origin)},
                    "destinations": destinations,
                },
                "journey_origin",
            )
        )
        index_entries.append(
            {
                "line_id": line_id,
                "direction": direction,
                "origin_naptan_id": origin,
                "path": path,
                "destination_naptan_ids": [d["naptan_id"] for d in destinations],
                "slots_published": sum(1 for s in slots[key] if s.status == STATUS_OK),
            }
        )
    index = {
        **_head(stamp, rows.slots),
        "stations": dict(sorted(names.items())),
        "origins": index_entries,
    }
    return [(JOURNEY_INDEX_PATH, index, "journey_index"), *documents]
