"""Compare int_inferred_arrivals with the Python reference on one real day (PLAN 7.2).

    AWS_PROFILE=mtd-dev uv run python scripts/check_arrival_parity.py --date 2026-11-06
    AWS_PROFILE=mtd-dev uv run python scripts/check_arrival_parity.py --date 2026-11-06 \\
        --station 940GZZLUOXC --station 940GZZLUKSX

Reads stg_arrivals and stg_polls for the day with an hour either side (an
arrival needs the station's next poll, and a repeat is collapsed against a
neighbouring arrival that may have been last seen before midnight), runs
``infer_arrivals_from_rows`` with the vehicle-id lines from
seed_vehicle_id_lines, and compares the arrivals last seen on the day with
the dev table, column by column. Read-only: SELECTs in the dev workgroup,
and the result files are read back from the workgroup's S3 output.
Exits 1 on any difference.
"""

import argparse
import csv
import io
import os
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import boto3

from mind_the_data.analysis.arrivals import (
    ARRIVAL_COLUMNS,
    InferenceParams,
    infer_arrivals_from_rows,
)
from mind_the_data.ops.athena import human_bytes, run_query

if TYPE_CHECKING:
    from mypy_boto3_athena import AthenaClient
    from mypy_boto3_s3 import S3Client

Row = dict[str, str]


def _select(
    athena: "AthenaClient", s3: "S3Client", sql: str, workgroup: str, database: str
) -> list[Row]:
    """Every row of a query, read from its CSV result file rather than paged."""
    result = run_query(athena, sql, workgroup=workgroup, database=database, max_rows=1)
    location = athena.get_query_execution(QueryExecutionId=result.query_id)["QueryExecution"][
        "ResultConfiguration"
    ]["OutputLocation"]
    bucket, _, key = location.removeprefix("s3://").partition("/")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
    rows = list(csv.DictReader(io.StringIO(body)))
    print(f"  {len(rows):>9,} rows, scanned {human_bytes(result.bytes_scanned)}", file=sys.stderr)
    return rows


def _station_filter(column: str, stations: list[str]) -> str:
    if not stations:
        return ""
    paths = ", ".join(f"'/StopPoint/{s}/Arrivals'" for s in stations)
    return f" and {column} in ({paths})"


def _normalise(row: Mapping[str, object]) -> tuple[object, ...]:
    values: list[object] = []
    for column in ARRIVAL_COLUMNS:
        value = row[column]
        if column == "arrival_time":
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
            value = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        elif column in ("last_seconds_to_arrival", "last_time_to_station"):
            value = int(str(value))
        elif value == "":
            value = None
        values.append(value)
    return tuple(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="UTC day")
    parser.add_argument("--station", action="append", default=[], help="NaPTAN id; repeatable")
    parser.add_argument(
        "--workgroup", default=os.environ.get("DBT_ATHENA_WORKGROUP_DEV", "mind-the-data-dev")
    )
    parser.add_argument(
        "--database", default=os.environ.get("DBT_ATHENA_SCHEMA_DEV", "mind_the_data_dev")
    )
    args = parser.parse_args(argv)
    if not args.workgroup.endswith("-dev"):
        parser.error("this script only queries a dev workgroup")

    day: date = args.date
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    lead_in = start - timedelta(hours=1)
    tail = end + timedelta(hours=1)
    window = (
        f"polled_at >= timestamp '{lead_in:%Y-%m-%d %H:%M:%S}'"
        f" and polled_at < timestamp '{tail:%Y-%m-%d %H:%M:%S}'"
        f" and dt between date '{lead_in.date()}' and date '{tail.date()}'"
    )
    athena = boto3.client("athena")
    s3 = boto3.client("s3")
    query = (args.workgroup, args.database)

    print("seed_vehicle_id_lines", file=sys.stderr)
    seed = _select(
        athena, s3, "select line_id from seed_vehicle_id_lines where link_by_vehicle_id", *query
    )
    params = InferenceParams(vehicle_id_lines=frozenset(r["line_id"] for r in seed))
    print("stg_polls", file=sys.stderr)
    polls = _select(
        athena,
        s3,
        "select poll_id, request_path, polled_at from stg_polls"
        f" where feed = 'arrivals' and is_ok and {window}"
        + _station_filter("request_path", args.station),
        *query,
    )
    print("stg_arrivals", file=sys.stderr)
    predictions = _select(
        athena,
        s3,
        "select poll_id, request_path, naptan_id, line_id, platform_name, direction,"
        " prediction_id, vehicle_id, expected_arrival, time_to_station from stg_arrivals"
        f" where {window}" + _station_filter("request_path", args.station),
        *query,
    )
    print("int_inferred_arrivals", file=sys.stderr)
    sql_rows = _select(
        athena,
        s3,
        f"select {', '.join(ARRIVAL_COLUMNS)} from int_inferred_arrivals"
        f" where last_seen_at >= timestamp '{start:%Y-%m-%d %H:%M:%S}'"
        f" and last_seen_at < timestamp '{end:%Y-%m-%d %H:%M:%S}'"
        + _station_filter("request_path", args.station),
        *query,
    )

    last_seen = {
        (r["request_path"], r["poll_id"]): datetime.fromisoformat(r["polled_at"]) for r in polls
    }
    python_rows = [
        row
        for row in infer_arrivals_from_rows(predictions, polls, params)
        if start.replace(tzinfo=None)
        <= last_seen[(str(row["request_path"]), str(row["last_poll_id"]))]
        < end.replace(tzinfo=None)
    ]

    python = Counter(_normalise(r) for r in python_rows)
    sql = Counter(_normalise(r) for r in sql_rows)
    only_python = python - sql
    only_sql = sql - python
    lines = Counter(str(r["line_id"]) for r in python_rows)
    print(f"vehicle-id lines: {sorted(params.vehicle_id_lines)}")
    print(f"python arrivals: {sum(python.values()):,}; sql arrivals: {sum(sql.values()):,}")
    print("per line: " + ", ".join(f"{k} {v:,}" for k, v in sorted(lines.items())))
    for label, diff in (("only in python", only_python), ("only in sql", only_sql)):
        for row, count in sorted(diff.items(), key=str)[:20]:
            print(f"{label} x{count}: {dict(zip(ARRIVAL_COLUMNS, row, strict=True))}")
    if only_python or only_sql:
        print(
            f"MISMATCH: {sum(only_python.values())} only in python, {sum(only_sql.values())} only in sql"
        )
        return 1
    print("identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
