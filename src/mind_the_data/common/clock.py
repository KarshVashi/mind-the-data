"""UTC time helpers.

Everything stored by this project is UTC. London-time logic lives only in dbt,
so these helpers refuse naive datetimes rather than guessing a zone.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` converted to UTC; reject naive datetimes."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime: attach a timezone before storing")
    return value.astimezone(UTC)


def isoformat_ms(value: datetime) -> str:
    """Format as ``2026-11-08T08:15:00.123Z``, the envelope timestamp format."""
    utc = ensure_utc(value)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
