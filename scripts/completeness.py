"""Report collection completeness from the raw bucket (M1 deployed acceptance).

    AWS_PROFILE=mtd-dev uv run python scripts/completeness.py --hours 24

Read-only: lists and reads raw objects. Exits non-zero when coverage is below
the thresholds (99% of minutes for line status, 97% for arrivals by default).
"""

import argparse
import json
import os
import sys
from datetime import datetime

import boto3

from mind_the_data.common.clock import ensure_utc, utc_now
from mind_the_data.common.raw import Feed
from mind_the_data.quality.completeness import (
    FeedCompleteness,
    assess,
    gaps,
    load_feed,
    window_minutes,
)


def _bucket(explicit: str | None) -> str:
    if explicit:
        return explicit
    if os.environ.get("RAW_BUCKET"):
        return os.environ["RAW_BUCKET"]
    account = boto3.client("sts").get_caller_identity()["Account"]
    return f"mind-the-data-raw-{account}"


def _line(result: FeedCompleteness, threshold: float) -> str:
    verdict = "ok" if result.coverage >= threshold else "BELOW"
    return (
        f"{result.feed.value:<12} {result.covered:>5}/{result.minutes:<5} minutes "
        f"{result.coverage:7.2%} (target {threshold:.0%}, {verdict}); "
        f"objects {result.objects}, request success {result.request_success:.2%}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--end", help="window end, ISO 8601 UTC (default: now)")
    parser.add_argument("--bucket", help="raw bucket (default: RAW_BUCKET or derived)")
    parser.add_argument("--status-target", type=float, default=0.99)
    parser.add_argument("--arrivals-target", type=float, default=0.97)
    parser.add_argument(
        "--arrivals-min-ok-share",
        type=float,
        default=0.9,
        help="share of station requests that must succeed for an arrivals minute to count",
    )
    parser.add_argument(
        "--workers", type=int, default=16, help="concurrent object reads (default 16)"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    end: datetime = ensure_utc(datetime.fromisoformat(args.end)) if args.end else utc_now()
    minutes = window_minutes(end, args.hours)
    bucket = _bucket(args.bucket)
    s3 = boto3.client("s3")

    checks = [
        (Feed.LINE_STATUS, 1.0, args.status_target),
        (Feed.ARRIVALS, args.arrivals_min_ok_share, args.arrivals_target),
    ]
    results = []
    passed = True
    for feed, min_ok_share, target in checks:
        result = assess(
            feed, minutes, load_feed(s3, bucket, feed, minutes, args.workers), min_ok_share
        )
        results.append((result, target))
        passed &= result.coverage >= target

    if args.json:
        print(
            json.dumps(
                {
                    "bucket": bucket,
                    "window_start": minutes[0].isoformat(),
                    "window_end": end.isoformat(),
                    "passed": passed,
                    "feeds": {
                        r.feed.value: {
                            "minutes": r.minutes,
                            "covered": r.covered,
                            "coverage": round(r.coverage, 4),
                            "target": t,
                            "objects": r.objects,
                            "request_success": round(r.request_success, 4),
                            "gaps": [
                                [a.isoformat(), b.isoformat()] for a, b in gaps(r.missing_minutes)
                            ],
                        }
                        for r, t in results
                    },
                },
                indent=2,
            )
        )
    else:
        print(f"raw bucket {bucket}, {minutes[0]:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC")
        for result, target in results:
            print(_line(result, target))
            for start, stop in gaps(result.missing_minutes)[:10]:
                print(f"    gap {start:%Y-%m-%d %H:%M} to {stop:%H:%M} UTC")
        print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
