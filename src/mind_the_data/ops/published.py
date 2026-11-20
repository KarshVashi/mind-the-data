"""Freshness of the published output, read over HTTP like a visitor would.

Collection has its own alarms. This checks the other end: that Monday's publish
and the site deploy actually landed. Pure checks take the parsed JSON so tests
never touch the network; only :func:`fetch_json` does I/O.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from mind_the_data.ops.health import Section

BASE_URL = "https://data.karshvashi.com"
LONDON = ZoneInfo("Europe/London")
MAX_AGE = timedelta(days=8)
# The weekly publish runs at 06:30 London on Mondays; the grace covers a slow
# build and the deploy hook before a missing new week counts as a failure.
PUBLISH_TIME = timedelta(hours=6, minutes=30)
PUBLISH_GRACE = timedelta(hours=2)


def fetch_json(url: str, client: httpx.Client) -> dict[str, Any]:
    response = client.get(url, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError(f"{url} is not a JSON object")
    return body


def expected_iso_week(now: datetime) -> str:
    """The last complete ISO week that should be live at ``now``.

    Before Monday's publish plus grace, that is still the week before.
    """
    shifted = now.astimezone(LONDON) - PUBLISH_TIME - PUBLISH_GRACE
    year, week, _ = (shifted.date() - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def _major(version: object) -> str | None:
    return str(version).split(".")[0] if isinstance(version, str) and version else None


def check_published(
    index: dict[str, Any], latest: dict[str, Any], now: datetime, code_schema_version: str
) -> list[str]:
    """Return one problem per failed rule; an empty list means fresh."""
    problems: list[str] = []
    for name, doc in (("meta/index.json", index), ("league/latest.json", latest)):
        raw = doc.get("generated_at")
        try:
            generated = datetime.fromisoformat(str(raw))
        except ValueError:
            problems.append(f"{name}: generated_at is missing or unreadable ({raw!r})")
            continue
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=UTC)
        age = now - generated
        if age > MAX_AGE:
            problems.append(f"{name}: generated_at {raw} is {age.days} days old (limit 8)")

    week = (latest.get("data_window") or {}).get("iso_week")
    expected = expected_iso_week(now)
    if week != expected:
        problems.append(f"league/latest.json: ISO week is {week}, expected {expected}")

    for name, doc in (("meta/index.json", index), ("league/latest.json", latest)):
        if _major(doc.get("schema_version")) != _major(code_schema_version):
            problems.append(
                f"{name}: schema_version {doc.get('schema_version')!r} has a different major "
                f"from the code's {code_schema_version}"
            )
    return problems


def published_section(
    index: dict[str, Any] | None,
    latest: dict[str, Any] | None,
    now: datetime,
    code_schema_version: str,
    fetch_error: str | None = None,
) -> Section:
    section = Section("Published output")
    if fetch_error is not None or index is None or latest is None:
        # Unreachable files are the failure this check exists to catch.
        reason = fetch_error or "no data"
        section.lines.append(f"Could not fetch the published files: {reason}")
        section.problems.append(f"Published output: could not fetch ({reason})")
        return section
    problems = check_published(index, latest, now, code_schema_version)
    week = (latest.get("data_window") or {}).get("iso_week")
    section.lines.append(
        f"generated_at {latest.get('generated_at')}, ISO week {week}, "
        f"schema {latest.get('schema_version')}."
    )
    section.problems.extend(problems)
    section.lines.extend(f"- {p}" for p in problems)
    return section


def fetch_section(now: datetime, code_schema_version: str, base_url: str = BASE_URL) -> Section:
    try:
        with httpx.Client(timeout=20.0) as client:
            index = fetch_json(f"{base_url}/meta/index.json", client)
            latest = fetch_json(f"{base_url}/league/latest.json", client)
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        return published_section(None, None, now, code_schema_version, error)
    return published_section(index, latest, now, code_schema_version)
