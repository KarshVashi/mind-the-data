"""Build the ops workflows' health reports (read-only).

    # previous UTC day: completeness JSON, alarms, Lambda errors, raw freshness
    uv run python scripts/ops_report.py daily --completeness completeness.json
    # service hours: the strict arrivals check over the last 15 minutes of raw
    uv run python scripts/ops_report.py daytime

Writes Markdown to --out (default stdout). Exits 0 when every target is met,
3 when something missed it (distinct from a crash, which exits 1).
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import boto3

from mind_the_data.common.clock import utc_now
from mind_the_data.common.config import load_config
from mind_the_data.ops.arrivals_check import (
    expected_lines,
    recent_polls,
    strict_check,
    strict_section,
)
from mind_the_data.ops.health import (
    LIVE_FEEDS,
    Section,
    alarm_section,
    alarms_fired,
    completeness_section,
    current_alarms,
    day_bounds,
    freshness_section,
    guarded,
    lambda_section,
    render,
)

MISSED = 3


def _bucket() -> str:
    if os.environ.get("RAW_BUCKET"):
        return os.environ["RAW_BUCKET"]
    account = boto3.client("sts").get_caller_identity()["Account"]
    return f"mind-the-data-raw-{account}"


def daily(day: date, completeness_file: Path, now: datetime) -> list[Section]:
    start, end = day_bounds(day)
    cloudwatch = boto3.client("cloudwatch")
    text = completeness_file.read_text() if completeness_file.exists() else ""
    return [
        completeness_section(text),
        guarded(
            "Alarms",
            lambda: alarm_section(
                current_alarms(cloudwatch), alarms_fired(cloudwatch, start, end), now
            ),
        ),
        guarded("Lambda errors", lambda: lambda_section(cloudwatch, start, end)),
        guarded(
            "Newest raw object per feed",
            lambda: freshness_section(boto3.client("s3"), _bucket(), now),
        ),
    ]


def daytime(now: datetime) -> list[Section]:
    _, stations = load_config()
    expected = expected_lines(stations)
    names = {s.naptan_id: s.name for s in stations}

    def check() -> Section:
        polls = recent_polls(boto3.client("s3"), _bucket(), now)
        return strict_section(expected, strict_check(expected, polls), names)

    return [
        guarded("Arrivals, strict", check),
        guarded(
            "Newest raw object per feed",
            lambda: freshness_section(boto3.client("s3"), _bucket(), now, LIVE_FEEDS),
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["daily", "daytime"])
    parser.add_argument("--day", help="daily: UTC day, YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--completeness", type=Path, default=Path("completeness.json"))
    parser.add_argument("--out", type=Path, help="write the Markdown here")
    parser.add_argument("--footer", default="", help="appended to the report, e.g. a run link")
    args = parser.parse_args(argv)

    now = utc_now()
    if args.kind == "daily":
        day = date.fromisoformat(args.day) if args.day else (now - timedelta(days=1)).date()
        sections = daily(day, args.completeness, now)
        title = f"Collector health for {day:%Y-%m-%d} (UTC)"
    else:
        sections = daytime(now)
        title = f"Daytime arrivals check at {now:%Y-%m-%d %H:%M} UTC"

    report = render(title, sections, args.footer)
    if args.out:
        args.out.write_text(report)
    else:
        print(report)
    return MISSED if any(s.problems for s in sections) else 0


if __name__ == "__main__":
    sys.exit(main())
