"""Run one read-only query in the dev Athena workgroup and print the result.

    AWS_PROFILE=mtd-dev uv run python scripts/athena_query.py \
        "select feed, count(*) from mind_the_data_raw.line_status where dt = '2026-11-08' group by 1"
    uv run python scripts/athena_query.py --file analyses/check.sql

Only a single SELECT, WITH, SHOW or DESCRIBE is accepted. Bytes scanned are
always printed, so the cost of a query is visible.
"""

import argparse
import os
import sys
from pathlib import Path

import boto3

from mind_the_data.ops.athena import QueryRefused, format_table, human_bytes, run_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("sql", nargs="?", help="the query")
    source.add_argument("--file", type=Path, help="read the query from a file")
    parser.add_argument(
        "--workgroup", default=os.environ.get("DBT_ATHENA_WORKGROUP_DEV", "mind-the-data-dev")
    )
    parser.add_argument(
        "--database", default=os.environ.get("DBT_ATHENA_SCHEMA_DEV", "mind_the_data_dev")
    )
    parser.add_argument("--max-rows", type=int, default=100)
    args = parser.parse_args(argv)

    if not args.workgroup.endswith("-dev"):
        parser.error("this script only queries a dev workgroup")
    sql = args.file.read_text() if args.file else args.sql

    try:
        result = run_query(
            boto3.client("athena"),
            sql,
            workgroup=args.workgroup,
            database=args.database,
            max_rows=args.max_rows,
        )
    except QueryRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(format_table(result))
    print(
        f"\n{len(result.rows)} row(s) shown; scanned {human_bytes(result.bytes_scanned)} "
        f"in {result.elapsed_ms} ms; query id {result.query_id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
