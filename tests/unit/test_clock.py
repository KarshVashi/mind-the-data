from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from mind_the_data.common.clock import ensure_utc, isoformat_ms, utc_now


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.utcoffset() == timedelta(0)


def test_ensure_utc_converts_bst_to_utc() -> None:
    bst = timezone(timedelta(hours=1))
    local = datetime(2026, 7, 1, 8, 15, tzinfo=bst)
    assert ensure_utc(local) == datetime(2026, 7, 1, 7, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("local", "expected_utc"),
    [
        # 2026-10-25 01:30 London happens twice; fold picks BST then GMT.
        (
            datetime(2026, 10, 25, 1, 30, fold=0),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 25, 1, 30, fold=1),
            datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
        ),
        # Either side of the clock change.
        (datetime(2026, 10, 24, 8, 0), datetime(2026, 10, 24, 7, 0, tzinfo=UTC)),
        (datetime(2026, 10, 26, 8, 0), datetime(2026, 10, 26, 8, 0, tzinfo=UTC)),
    ],
)
def test_ensure_utc_across_london_clock_change(
    local: datetime, expected_utc: datetime
) -> None:
    assert ensure_utc(local.replace(tzinfo=ZoneInfo("Europe/London"))) == expected_utc


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 11, 8, 8, 15))


def test_isoformat_ms_matches_envelope_format() -> None:
    value = datetime(2026, 11, 8, 8, 15, 0, 123456, tzinfo=UTC)
    assert isoformat_ms(value) == "2026-11-08T08:15:00.123Z"


def test_isoformat_ms_pads_milliseconds() -> None:
    value = datetime(2026, 11, 8, 8, 15, 0, 5000, tzinfo=UTC)
    assert isoformat_ms(value) == "2026-11-08T08:15:00.005Z"
