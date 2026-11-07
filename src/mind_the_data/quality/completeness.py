"""Collection completeness over a time window, read straight from the raw bucket.

A minute counts as covered for a feed when that minute's raw object exists and
enough of its requests succeeded:

- line status: the one status request succeeded;
- arrivals: at least ``min_ok_share`` of the station requests succeeded.

Missing objects and failure envelopes both count against coverage, matching
the rule that missing data is never treated as a good minute.
"""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mind_the_data.common.clock import ensure_utc
from mind_the_data.common.raw import Envelope, Feed, decode_ndjson_gz

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_STAMP = re.compile(r"_(\d{8}T\d{6}Z)_[0-9a-f-]{36}\.ndjson\.gz$")


def minute_of_key(key: str) -> datetime | None:
    """The UTC minute a raw object's poll started in, from its key."""
    match = _STAMP.search(key)
    if match is None:
        return None
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return stamp.replace(second=0)


def window_minutes(end: datetime, hours: float) -> list[datetime]:
    """Every whole UTC minute in [end - hours, end)."""
    last = ensure_utc(end).replace(second=0, microsecond=0)
    count = round(hours * 60)
    return [last - timedelta(minutes=i) for i in range(count, 0, -1)]


def hour_prefixes(feed: Feed, minutes: Sequence[datetime]) -> list[str]:
    hours = sorted({m.replace(minute=0) for m in minutes})
    return [f"raw/{feed.value}/dt={h:%Y-%m-%d}/hour={h:%H}/" for h in hours]


def minute_ok(envelopes: Sequence[Envelope], min_ok_share: float) -> bool:
    if not envelopes:
        return False
    ok = sum(e.ok for e in envelopes)
    return ok / len(envelopes) >= min_ok_share


@dataclass
class FeedCompleteness:
    feed: Feed
    minutes: int
    covered: int = 0
    objects: int = 0
    requests: int = 0
    ok_requests: int = 0
    missing_minutes: list[datetime] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.covered / self.minutes if self.minutes else 0.0

    @property
    def request_success(self) -> float:
        return self.ok_requests / self.requests if self.requests else 0.0


def assess(
    feed: Feed,
    minutes: Sequence[datetime],
    objects: Mapping[datetime, Sequence[Sequence[Envelope]]],
    min_ok_share: float,
) -> FeedCompleteness:
    """Coverage for one feed. ``objects`` maps a minute to the objects found in it."""
    result = FeedCompleteness(feed=feed, minutes=len(minutes))
    wanted = set(minutes)
    for minute, found in objects.items():
        if minute not in wanted:
            continue
        for envelopes in found:
            result.objects += 1
            result.requests += len(envelopes)
            result.ok_requests += sum(e.ok for e in envelopes)
    for minute in minutes:
        found = objects.get(minute, [])
        if any(minute_ok(envs, min_ok_share) for envs in found):
            result.covered += 1
        else:
            result.missing_minutes.append(minute)
    return result


def gaps(missing: Sequence[datetime]) -> list[tuple[datetime, datetime]]:
    """Collapse missing minutes into [start, end] runs, for reporting."""
    runs: list[tuple[datetime, datetime]] = []
    for minute in sorted(missing):
        if runs and minute - runs[-1][1] == timedelta(minutes=1):
            runs[-1] = (runs[-1][0], minute)
        else:
            runs.append((minute, minute))
    return runs


def _keys(s3: "S3Client", bucket: str, prefixes: Iterable[str]) -> Iterator[str]:
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]


def load_feed(
    s3: "S3Client",
    bucket: str,
    feed: Feed,
    minutes: Sequence[datetime],
    workers: int = 1,
) -> dict[datetime, list[list[Envelope]]]:
    """Read every object for ``feed`` whose poll minute falls in the window.

    A day is about 1,440 objects per feed, and one GET at a time takes several
    minutes from outside the region, so ``workers`` fetches them concurrently
    (boto3 clients are thread-safe).
    """
    wanted = set(minutes)
    keyed: list[tuple[datetime, str]] = []
    for key in _keys(s3, bucket, hour_prefixes(feed, minutes)):
        minute = minute_of_key(key)
        if minute is not None and minute in wanted:
            keyed.append((minute, key))

    def fetch(key: str) -> list[Envelope]:
        return decode_ndjson_gz(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        decoded = list(pool.map(fetch, [key for _, key in keyed]))
    found: dict[datetime, list[list[Envelope]]] = {}
    for (minute, _), envelopes in zip(keyed, decoded, strict=True):
        found.setdefault(minute, []).append(envelopes)
    return found
