"""The strict arrivals check: every configured line is seen at its station.

Overnight a board can be empty simply because no train is due, so this check
only means something during service hours. It reads what the collector
already stored in raw rather than calling TfL, which keeps the TfL key out of
CI entirely and tests the data the league table is built from.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from mind_the_data.common.config import Station
from mind_the_data.common.raw import Envelope, Feed, decode_ndjson_gz
from mind_the_data.ops.health import Section
from mind_the_data.quality.completeness import hour_prefixes, minute_of_key

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

# Predictions on a daytime board reach about half an hour ahead, so a quarter
# of an hour of polls covers every line, even the least frequent.
DEFAULT_WINDOW = timedelta(minutes=15)


def expected_lines(stations: Iterable[Station]) -> dict[str, set[str]]:
    """Configured line ids per station id."""
    expected: dict[str, set[str]] = {}
    for station in stations:
        expected.setdefault(station.naptan_id, set()).add(station.line_id)
    return expected


def lines_seen(payload: Any, naptan_id: str) -> set[str]:
    """Line ids with a prediction at ``naptan_id`` in one Arrivals response."""
    if not isinstance(payload, list):
        return set()
    return {
        p["lineId"]
        for p in payload
        if isinstance(p, dict) and p.get("naptanId") == naptan_id and "lineId" in p
    }


def naptan_of(path: str) -> str | None:
    """The StopPoint id in an Arrivals request path, ``/StopPoint/{id}/Arrivals``."""
    parts = path.split("?")[0].strip("/").split("/")
    if len(parts) == 3 and parts[0] == "StopPoint" and parts[2] == "Arrivals":
        return parts[1]
    return None


@dataclass
class StrictResult:
    polls: int = 0
    seen: dict[str, set[str]] = field(default_factory=dict)
    failed_requests: dict[str, int] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)


def strict_check(
    expected: Mapping[str, set[str]], polls: Sequence[Sequence[Envelope]]
) -> StrictResult:
    """Union the lines seen across ``polls`` and list what is missing."""
    result = StrictResult(polls=len(polls))
    for envelopes in polls:
        for env in envelopes:
            naptan = naptan_of(env.request.path)
            if naptan is None:
                continue
            if not env.ok:
                result.failed_requests[naptan] = result.failed_requests.get(naptan, 0) + 1
                continue
            result.seen.setdefault(naptan, set()).update(lines_seen(env.payload, naptan))
    for naptan, lines in sorted(expected.items()):
        missing = sorted(lines - result.seen.get(naptan, set()))
        if missing:
            result.missing[naptan] = missing
    return result


def recent_polls(
    s3: "S3Client", bucket: str, now: datetime, window: timedelta = DEFAULT_WINDOW
) -> list[list[Envelope]]:
    """Every arrivals object whose poll minute is in [now - window, now)."""
    count = int(window.total_seconds() // 60)
    last = now.replace(second=0, microsecond=0)
    minutes = {last - timedelta(minutes=i) for i in range(1, count + 1)}
    polls: list[list[Envelope]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in hour_prefixes(Feed.ARRIVALS, sorted(minutes)):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if minute_of_key(obj["Key"]) in minutes:
                    body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    polls.append(decode_ndjson_gz(body))
    return polls


def strict_section(
    expected: Mapping[str, set[str]], result: StrictResult, names: Mapping[str, str]
) -> Section:
    section = Section("Arrivals, strict")
    pairs = sum(len(lines) for lines in expected.values())
    missing_pairs = sum(len(lines) for lines in result.missing.values())
    section.lines.append(
        f"{result.polls} polls read; {pairs - missing_pairs}/{pairs} station-line pairs "
        f"seen at {len(expected)} stations."
    )
    if result.polls == 0:
        section.problems.append("arrivals: no arrivals objects in the window")
        return section
    if result.missing:
        section.lines.append("\n| station | missing lines | failed requests |")
        section.lines.append("| --- | --- | --- |")
        for naptan, lines in result.missing.items():
            label = f"{names.get(naptan, naptan)} ({naptan})"
            failed = result.failed_requests.get(naptan, 0)
            section.lines.append(f"| {label} | {', '.join(lines)} | {failed} |")
            section.problems.append(
                f"arrivals: {label} shows no predictions for {', '.join(lines)}"
            )
    return section
