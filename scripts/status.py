"""One read-only screen of project health: PRs, workflow runs, alarms, raw data, ops issues.

    make status                       # AWS parts use the mtd-dev profile
    uv run python scripts/status.py --profile other-profile

Anything it cannot reach (no ``gh`` login, no AWS session) is reported as
unavailable and the rest still prints. Run ``aws sso login --sso-session mtd``
first for the AWS parts.
"""

import argparse
import os
import sys
from functools import cache

import boto3
from botocore.config import Config

from mind_the_data.common.clock import utc_now
from mind_the_data.ops.github import run_gh
from mind_the_data.ops.status import collect, render_text

# Fail fast rather than hang when offline: this is meant to take seconds.
_FAST = Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=os.environ.get("MTD_PROFILE", "mtd-dev"))
    parser.add_argument("--region", default="eu-west-2")
    args = parser.parse_args(argv)

    @cache
    def session() -> boto3.Session:
        return boto3.Session(profile_name=args.profile, region_name=args.region)

    def bucket() -> str:
        if os.environ.get("RAW_BUCKET"):
            return os.environ["RAW_BUCKET"]
        sts = session().client("sts", config=_FAST)
        return f"mind-the-data-raw-{sts.get_caller_identity()['Account']}"

    sections = collect(
        run_gh,
        lambda: session().client("cloudwatch", config=_FAST),
        lambda: session().client("s3", config=_FAST),
        bucket,
        utc_now(),
    )
    print(render_text(sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
