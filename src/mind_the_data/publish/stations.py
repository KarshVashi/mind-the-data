"""stations/index.json and stations/{naptan_id}.json (M8, every station).

The index carries a compact summary of the export week per station so the
site's map can colour every station from one file; each detail file holds the
full weekly history per line and direction, and the weekly disruption history.

Missing data is never good service: a station with no measured rows for the
week is `not_measured` with the reason `no_line_arrivals_data`, and a station
with no disruption row has `disruption: null` (the feed did not observe it),
never zero hours.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from mind_the_data.publish.documents import Stamp, _round, envelope, iso_week_of
from mind_the_data.publish.league import Window
from mind_the_data.publish.marts import (
    StationDisruptionRow,
    StationInfo,
    StationMeasuredRow,
)

NO_DATA_REASON = "no_line_arrivals_data"
STATION_INDEX_PATH = "stations/index.json"
STATION_PATH_TEMPLATE = "stations/{naptan_id}.json"


def station_path(naptan_id: str) -> str:
    return STATION_PATH_TEMPLATE.format(naptan_id=naptan_id)


def _station(info: StationInfo) -> dict[str, Any]:
    return {
        "naptan_id": info.naptan_id,
        "name": info.station_name,
        "lat": info.lat,
        "lon": info.lon,
        "lines": list(info.line_ids),
        "modes": list(info.modes),
        "is_measurement_station": info.is_measurement_station,
    }


def _disruption(row: StationDisruptionRow) -> dict[str, Any]:
    return {
        "iso_week": iso_week_of(row.week_start),
        "week_start": row.week_start.isoformat(),
        "week_end": (row.week_start + timedelta(days=6)).isoformat(),
        "closure_hours": _round(row.closure_hours, 2),
        "step_free_hours": _round(row.step_free_hours, 2),
        "other_disruption_hours": _round(row.other_disruption_hours, 2),
        "disruptions": row.disruptions,
        "observed_share": _round(row.observed_share, 4),
    }


def _measured_week(row: StationMeasuredRow) -> dict[str, Any]:
    return {
        "iso_week": iso_week_of(row.week_start),
        "week_start": row.week_start.isoformat(),
        "observed_hours": _round(row.observed_hours, 2),
        "trains_observed": row.trains_observed,
        "trains_per_hour": _round(row.trains_per_hour, 2),
        "median_gap_minutes": _round(row.median_gap_minutes, 2),
        "long_gaps_per_100_hours": _round(row.long_gaps_per_100_hours, 2),
        "scheduled_trains": row.scheduled_trains,
        "trains_on_boards_share": _round(row.trains_on_boards_share, 4),
        "excess_wait_minutes": _round(row.excess_wait_minutes, 2),
        "gate_judged_hours": row.gate_judged_hours,
        "gate_pass_share": _round(row.gate_pass_share, 4),
        "measured_status": row.measured_status,
        "measured_withheld_reason": row.measured_withheld_reason,
    }


def rollup(rows: Sequence[StationMeasuredRow]) -> dict[str, Any]:
    """One status for a station-week from its line and direction rows."""
    measured = [r for r in rows if r.measured_status == "measured"]
    if measured:
        status = "measured"
    elif any(r.measured_status == "withheld" for r in rows):
        status = "withheld"
    else:
        status = "not_measured"
    if not rows:
        reasons = [NO_DATA_REASON]
    else:
        reasons = sorted(
            {
                r.measured_withheld_reason or r.measured_status
                for r in rows
                if r.measured_status != "measured"
            }
        )
    return {
        "measured_status": status,
        "status_reasons": reasons,
        "directions_measured": len(measured),
        "directions_total": len(rows),
    }


def _summary(
    week: Window, measured: Sequence[StationMeasuredRow], disruption: StationDisruptionRow | None
) -> dict[str, Any]:
    this_week = [r for r in measured if r.week_start == week.start]
    return {
        "iso_week": iso_week_of(week.start),
        **rollup(this_week),
        "disruption": None
        if disruption is None
        else {
            key: value
            for key, value in _disruption(disruption).items()
            if key not in ("iso_week", "week_start", "week_end")
        },
    }


class _Grouped:
    """Rows up to the export week, grouped by station."""

    def __init__(
        self,
        window: Window,
        disruption: Sequence[StationDisruptionRow],
        measured: Sequence[StationMeasuredRow],
    ) -> None:
        self.window = window
        self.disruption: dict[str, list[StationDisruptionRow]] = defaultdict(list)
        self.measured: dict[str, list[StationMeasuredRow]] = defaultdict(list)
        for d in sorted(disruption, key=lambda r: r.week_start):
            if d.week_start <= window.start:
                self.disruption[d.naptan_id].append(d)
        for m in sorted(measured, key=lambda r: (r.week_start, r.line_id, r.direction)):
            if m.week_start <= window.start:
                self.measured[m.naptan_id].append(m)

    def summary(self, naptan_id: str) -> dict[str, Any]:
        current = next(
            (d for d in self.disruption[naptan_id] if d.week_start == self.window.start), None
        )
        return _summary(self.window, self.measured[naptan_id], current)


def station_index_document(
    stations: Sequence[StationInfo],
    disruption: Sequence[StationDisruptionRow],
    measured: Sequence[StationMeasuredRow],
    stamp: Stamp,
    window: Window,
) -> dict[str, Any]:
    grouped = _Grouped(window, disruption, measured)
    return {
        **envelope(stamp, window, None),
        "latest_week": iso_week_of(window.start),
        "stations": [
            {
                **_station(s),
                "path": station_path(s.naptan_id),
                "summary": grouped.summary(s.naptan_id),
            }
            for s in sorted(stations, key=lambda s: s.naptan_id)
        ],
    }


def station_document(
    station: StationInfo,
    disruption: Sequence[StationDisruptionRow],
    measured: Sequence[StationMeasuredRow],
    stamp: Stamp,
    window: Window,
) -> dict[str, Any]:
    grouped = _Grouped(window, disruption, measured)
    history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in grouped.measured[station.naptan_id]:
        history[(row.line_id, row.direction)].append(_measured_week(row))
    summary = grouped.summary(station.naptan_id)
    current = next(
        (d for d in grouped.disruption[station.naptan_id] if d.week_start == window.start), None
    )
    return {
        **envelope(stamp, window, None if current is None else current.observed_share),
        "station": _station(station),
        "summary": summary,
        "measured_history": [
            {"line_id": line_id, "direction": direction, "weeks": weeks}
            for (line_id, direction), weeks in sorted(history.items())
        ],
        "disruption_history": [_disruption(d) for d in grouped.disruption[station.naptan_id]],
    }
