"""Print the lines in scope and their route sequences, to help pick stations.

A one-off helper for maintaining config/lines.yaml and config/stations.yaml;
it is not run in CI. It always calls the TfL API anonymously (no key is read
from the environment) and sleeps between requests to stay far under the
anonymous rate limit.

Usage:
    uv run python scripts/discover_lines.py              # every line in scope
    uv run python scripts/discover_lines.py central dlr  # just these lines
    uv run python scripts/discover_lines.py --inbound    # inbound sequences too
"""

import argparse
import time
from typing import Any

import httpx

from mind_the_data.common.config import load_config

BASE_URL = "https://api.tfl.gov.uk"
MODES = "tube,elizabeth-line,dlr,overground,tram"
# About 20 requests a minute: well inside the anonymous allowance.
PAUSE_SECONDS = 3.0


def get_json(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    response.raise_for_status()
    time.sleep(PAUSE_SECONDS)
    return response.json()


def print_lines(lines: list[dict[str, Any]]) -> None:
    print(f"# Line/Mode/{MODES}: {len(lines)} lines")
    for line in lines:
        print(f"{line['id']:<20} {line['modeName']:<15} {line['name']}")
    print()


def print_route(client: httpx.Client, line_id: str, direction: str) -> None:
    data = get_json(client, f"/Line/{line_id}/Route/Sequence/{direction}")
    print(f"## {line_id} {direction}")
    for route in data.get("orderedLineRoutes", []):
        print(f"  route: {route['name']}")
    # stopPointSequences holds each branch with full station records; ids here
    # are the ones the Arrivals endpoint accepts.
    for seq in data.get("stopPointSequences", []):
        print(f"  branch {seq['branchId']} -> next {seq['nextBranchIds']}")
        for stop in seq["stopPoint"]:
            other = sorted({line["id"] for line in stop.get("lines", []) if line["id"] != line_id})
            extra = f"  [{', '.join(other)}]" if other else ""
            print(f"    {stop.get('stationId') or stop['id']:<16} {stop['name']}{extra}")
    print()


def check_arrivals(client: httpx.Client, expected: dict[str, set[str]]) -> int:
    """Call Arrivals once per station and compare the lines seen with the config.

    A line missing from a board can just mean no train is due (late night), so
    only a non-200 response counts as a failure; missing lines are reported.
    """
    failures = 0
    print(f"# Arrivals check: {len(expected)} distinct stations")
    for naptan_id, lines in sorted(expected.items()):
        response = client.get(f"/StopPoint/{naptan_id}/Arrivals")
        time.sleep(PAUSE_SECONDS)
        if response.status_code != 200:
            failures += 1
            print(f"{naptan_id:<14} HTTP {response.status_code}  FAIL")
            continue
        seen = {p["lineId"] for p in response.json() if p.get("naptanId") == naptan_id}
        missing = sorted(lines - seen)
        note = f"  no predictions now for {missing}" if missing else ""
        print(f"{naptan_id:<14} expected {sorted(lines)} seen {sorted(seen)}{note}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("line_ids", nargs="*", help="limit to these line ids")
    parser.add_argument("--inbound", action="store_true", help="also print inbound sequences")
    parser.add_argument(
        "--check-arrivals",
        action="store_true",
        help="call Arrivals for every station in config/stations.yaml instead",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        if args.check_arrivals:
            _, stations = load_config()
            expected: dict[str, set[str]] = {}
            for station in stations:
                expected.setdefault(station.naptan_id, set()).add(station.line_id)
            raise SystemExit(1 if check_arrivals(client, expected) else 0)

        lines: list[dict[str, Any]] = get_json(client, f"/Line/Mode/{MODES}")
        print_lines(lines)
        wanted = args.line_ids or [line["id"] for line in lines]
        directions = ["outbound", "inbound"] if args.inbound else ["outbound"]
        for line_id in wanted:
            for direction in directions:
                print_route(client, line_id, direction)


if __name__ == "__main__":
    main()
