from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from mind_the_data.common.raw import Envelope, Feed, RequestInfo
from mind_the_data.quality.completeness import (
    assess,
    gaps,
    hour_prefixes,
    minute_of_key,
    minute_ok,
    window_minutes,
)

T0 = datetime(2026, 11, 8, 8, 0, tzinfo=UTC)


def env(ok: bool, minute: datetime = T0, feed: Feed = Feed.ARRIVALS) -> Envelope:
    return Envelope(
        poll_id=UUID(int=0),
        polled_at=minute,
        feed=feed,
        request=RequestInfo(path="/x"),
        http_status=200 if ok else 503,
        error=None if ok else "http_503",
    )


def test_minute_of_key_reads_the_poll_stamp() -> None:
    key = f"raw/arrivals/dt=2026-11-08/hour=08/arrivals_20261108T081507Z_{uuid4()}.ndjson.gz"
    assert minute_of_key(key) == datetime(2026, 11, 8, 8, 15, tzinfo=UTC)
    assert minute_of_key("raw/arrivals/dt=2026-11-08/hour=08/other.txt") is None


def test_window_is_whole_minutes_ending_before_end() -> None:
    minutes = window_minutes(datetime(2026, 11, 8, 9, 0, 30, tzinfo=UTC), hours=0.05)
    assert minutes == [
        datetime(2026, 11, 8, 8, 57, tzinfo=UTC),
        datetime(2026, 11, 8, 8, 58, tzinfo=UTC),
        datetime(2026, 11, 8, 8, 59, tzinfo=UTC),
    ]


def test_window_of_24_hours_has_1440_minutes() -> None:
    assert len(window_minutes(T0, 24)) == 1440


def test_hour_prefixes_span_midnight() -> None:
    minutes = window_minutes(datetime(2026, 11, 9, 0, 30, tzinfo=UTC), hours=1)
    assert hour_prefixes(Feed.LINE_STATUS, minutes) == [
        "raw/line_status/dt=2026-11-08/hour=23/",
        "raw/line_status/dt=2026-11-09/hour=00/",
    ]


@pytest.mark.parametrize(
    ("oks", "share", "expected"),
    [
        ([True] * 9 + [False], 0.9, True),
        ([True] * 8 + [False] * 2, 0.9, False),
        ([True], 1.0, True),
        ([False], 1.0, False),
        ([], 0.0, False),
    ],
)
def test_minute_ok(oks: list[bool], share: float, expected: bool) -> None:
    assert minute_ok([env(ok) for ok in oks], share) is expected


def test_assess_hand_computed() -> None:
    # 10-minute window. Minute 3 has no object; minute 5's only request failed;
    # minute 7 has two objects (a retried poll), one good. Expect 8/10 covered.
    minutes = [T0 + timedelta(minutes=i) for i in range(10)]
    objects = {m: [[env(True, m)]] for m in minutes if m.minute not in (3, 5, 7)}
    objects[minutes[5]] = [[env(False, minutes[5])]]
    objects[minutes[7]] = [[env(False, minutes[7])], [env(True, minutes[7])]]
    objects[T0 + timedelta(minutes=30)] = [[env(True)]]  # outside the window: ignored

    result = assess(Feed.LINE_STATUS, minutes, objects, min_ok_share=1.0)

    assert (result.covered, result.minutes) == (8, 10)
    assert result.coverage == 0.8
    assert result.objects == 10
    assert (result.ok_requests, result.requests) == (8, 10)
    assert result.missing_minutes == [minutes[3], minutes[5]]


def test_gaps_collapse_consecutive_minutes() -> None:
    missing = [T0 + timedelta(minutes=i) for i in (1, 2, 3, 7, 9, 10)]
    assert gaps(missing) == [
        (T0 + timedelta(minutes=1), T0 + timedelta(minutes=3)),
        (T0 + timedelta(minutes=7), T0 + timedelta(minutes=7)),
        (T0 + timedelta(minutes=9), T0 + timedelta(minutes=10)),
    ]
