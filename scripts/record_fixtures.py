"""Record TfL API responses as test fixtures in tests/fixtures/tfl/.

Run once by hand: ``uv run python scripts/record_fixtures.py``. Pass
``--only NAME`` (repeatable) to record just those fixtures and leave the rest
untouched, so adding a fixture does not re-record ones that tests count on. It
calls the API anonymously (no key is read or sent) and paces requests well under the
anonymous limit. Each fixture wraps the unchanged response with the request
path, the HTTP status and the time it was recorded.

Fixtures must stay under the 1 MB pre-commit limit. When a response is
larger, the longest list in it is halved (keeping the first items) until the
file fits, and every cut is listed in the fixture's ``trimmed`` field. The cut
ignores meaning (it could, say, shorten a timetable's stationIntervals while
keeping journeys that point at them), so re-run the unit tests after any trim.
Nothing needed trimming at the last recording.

Any non-200 response aborts the run before a single file is written.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mind_the_data.common.urls import strip_app_key

BASE_URL = "https://api.tfl.gov.uk"
MODES = "tube,elizabeth-line,dlr,overground,tram"
OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tfl"
# Anonymous access allows a few hundred calls a minute at best; a 2 s gap keeps
# this script at 30 a minute or fewer.
PAUSE_SECONDS = 2.0
MAX_BYTES = 900 * 1024

ARRIVAL_STOPS = {
    "940GZZLUOXC": "Oxford Circus Underground Station",
    "940GZZDLCAN": "Canary Wharf DLR Station",
    "910GHGHI": "Highbury & Islington Rail Station (Overground)",
}

# Line-wide boards (M8): Bakerloo for a busy board of 940GZZ ids, the
# Elizabeth line for 910G ids, and Liberty, which is often empty at night.
LINE_ARRIVALS = ("bakerloo", "elizabeth", "liberty")

# (fixture name, request path)
REQUESTS: list[tuple[str, str]] = [
    ("line_status", f"/Line/Mode/{MODES}/Status"),
    ("line_disruption", f"/Line/Mode/{MODES}/Disruption"),
    ("stop_disruption", f"/StopPoint/Mode/{MODES}/Disruption"),
    *[(f"arrivals_{stop}", f"/StopPoint/{stop}/Arrivals") for stop in ARRIVAL_STOPS],
    *[(f"line_arrivals_{line}", f"/Line/{line}/Arrivals") for line in LINE_ARRIVALS],
    ("route_sequence_victoria_inbound", "/Line/victoria/Route/Sequence/inbound"),
    ("route_sequence_victoria_outbound", "/Line/victoria/Route/Sequence/outbound"),
    # Without a direction TfL answers with a disambiguation, not a timetable.
    ("timetable_victoria_940GZZLUOXC", "/Line/victoria/Timetable/940GZZLUOXC"),
    *[
        (
            f"timetable_victoria_940GZZLUOXC_{direction}",
            f"/Line/victoria/Timetable/940GZZLUOXC?direction={direction}",
        )
        for direction in ("inbound", "outbound")
    ],
    ("severity", "/Line/Meta/Severity"),
]


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _longest_list(node: Any, path: str = "$") -> tuple[str, list[Any]] | None:
    best: tuple[str, list[Any]] | None = None
    children: list[tuple[str, Any]] = []
    if isinstance(node, list):
        best = (path, node)
        children = [(f"{path}[{i}]", child) for i, child in enumerate(node)]
    elif isinstance(node, dict):
        children = [(f"{path}.{key}", child) for key, child in node.items()]
    for child_path, child in children:
        found = _longest_list(child, child_path)
        if found is not None and (best is None or len(found[1]) > len(best[1])):
            best = found
    return best


def trim_to_size(document: dict[str, Any]) -> None:
    """Halve the longest list in the payload until the fixture fits."""
    while len(_dump(document).encode("utf-8")) > MAX_BYTES:
        found = _longest_list(document["payload"], "$.payload")
        if found is None or len(found[1]) < 2:
            raise RuntimeError("fixture too large and nothing left to trim")
        path, items = found
        keep = len(items) // 2
        document["trimmed"].append(f"{path}: kept first {keep} of {len(items)}")
        del items[keep:]


def fetch(client: httpx.Client, name: str, path: str) -> dict[str, Any]:
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"{name}: HTTP {response.status_code}; no fixtures written")
    document: dict[str, Any] = {
        "request": {"path": strip_app_key(str(response.request.url.raw_path, "ascii"))},
        "http_status": response.status_code,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trimmed": [],
        "payload": response.json(),
    }
    trim_to_size(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record TfL responses as test fixtures.")
    parser.add_argument(
        "--only", action="append", metavar="NAME", help="record only this fixture (repeatable)"
    )
    args = parser.parse_args(argv)
    known = {name for name, _ in REQUESTS}
    unknown = sorted(set(args.only or ()) - known)
    if unknown:
        parser.error(f"unknown fixture names: {unknown}")
    selected = [(n, p) for n, p in REQUESTS if not args.only or n in args.only]

    headers = {"User-Agent": "mind-the-data fixture recorder"}
    documents: dict[str, dict[str, Any]] = {}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0) as client:
        for index, (name, path) in enumerate(selected):
            if index:
                time.sleep(PAUSE_SECONDS)
            documents[name] = fetch(client, name, path)
    # Write only after every request succeeded, so a failed run never leaves a
    # mix of old and new fixtures.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        out = OUT_DIR / f"{name}.json"
        out.write_text(_dump(document), encoding="utf-8")
        print(f"{name}: {out.stat().st_size} bytes, trimmed={document['trimmed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
