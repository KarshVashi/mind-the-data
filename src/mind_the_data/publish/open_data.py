"""The monthly open dataset (PLAN 8, M7): Parquet files under open-data/{yyyy-mm}/.

    uv run python -m mind_the_data.publish.open_data --month 2026-11 --dry-run
    uv run python -m mind_the_data.publish.open_data --month 2026-11 --publish-workgroup \\
        --out-dir build/open-data

Three datasets for one London calendar month, read from the prod marts:

- line_minute_status: the declared status of every line in every minute
  (int_line_minute_status), missing minutes included and flagged;
- line_daily: the declared-view daily facts (fct_line_daily);
- line_weekly: the declared-view weekly facts and ranks (fct_line_weekly)
  for the ISO weeks that end in the month, so every week appears in exactly
  one month and is complete by the 1st of the next.

The measured view is left out while it is unpublished (D42). Athena writes
the Parquet itself with UNLOAD, into a fresh prefix under the workgroup's
own results location; the files are then downloaded, checked and renamed.
Each month also gets README.md (columns, licence and the TfL attribution)
and manifest.json (rows, bytes and sha256 per file), whose JSON Schema is
publish/contract/open_data_manifest.schema.json.

--dry-run prints the statements and writes nothing; it needs no AWS access.
Uploading is mind_the_data.publish.upload, as for the weekly files.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from mind_the_data.ops.athena import check_read_only, run_query, wait_for_query
from mind_the_data.publish.documents import Stamp
from mind_the_data.publish.export import (
    DEFAULT_DATABASE,
    DEFAULT_WORKGROUP,
    PUBLISH_WORKGROUP,
    REPO_ROOT,
    git_sha,
    workgroup_for,
)
from mind_the_data.publish.schemas import SCHEMA_VERSION, validate

if TYPE_CHECKING:
    from mypy_boto3_athena import AthenaClient
    from mypy_boto3_s3 import S3Client

LONDON = ZoneInfo("Europe/London")
ATTRIBUTION = "Powered by TfL Open Data"
TFL_TERMS_URL = "https://tfl.gov.uk/corporate/terms-and-conditions/transport-data-service"
# D63: the recommended licence for the project's derived figures, pending the owner.
LICENCE_NAME = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

PARQUET_MAGIC = b"PAR1"
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
# A bucket name and a prefix of plain path segments (none of them . or ..),
# ending in a slash.
_S3_PREFIX = re.compile(
    r"s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/((?:(?!\.\.?/)[A-Za-z0-9_.=-]+/)+)"
)


class OpenDataError(RuntimeError):
    """The month cannot be exported as asked."""


@dataclass(frozen=True)
class Column:
    name: str
    # Athena type the column is cast to, so the Parquet schema does not drift with the marts.
    type: str
    description: str


@dataclass(frozen=True)
class Dataset:
    name: str
    source: str
    description: str
    grain: str
    # The London date column that places a row in a month.
    month_column: str
    columns: tuple[Column, ...]
    order_by: tuple[str, ...]


_LINE = Column("line_id", "varchar", "TfL line id, e.g. `victoria`.")

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        name="line_minute_status",
        source="int_line_minute_status",
        description=(
            "The declared status of each line in each minute: the worst status TfL "
            "reported for the line in the minute's poll (ADR-004). Minutes without a "
            "known status are kept and flagged `is_missing`; they are never Good Service."
        ),
        grain="line x UTC minute",
        month_column="london_date",
        columns=(
            _LINE,
            Column("minute_utc", "timestamp(3)", "Start of the minute, UTC."),
            Column("london_date", "date", "London calendar date of the minute."),
            Column("day_type", "varchar", "Day type of the London date."),
            Column("time_band", "varchar", "London time band of the minute."),
            Column("time_weight", "double", "Weight of the time band in the Disruption Index."),
            Column("is_scheduled", "boolean", "Inside the line's scheduled hours."),
            Column(
                "status_severity",
                "integer",
                "TfL severity code of the worst status; null if missing.",
            ),
            Column("status_severity_description", "varchar", "TfL's text for that code."),
            Column("reason", "varchar", "TfL's reason text for the worst status, if any."),
            Column("is_planned", "boolean", "The worst status is filed as planned work."),
            Column("severity_weight", "double", "The project's weight for the worst status."),
            Column("is_missing", "boolean", "No status is known for the line in this minute."),
            Column(
                "missing_reason",
                "varchar",
                "`no_successful_poll`, `line_not_in_poll` or `no_current_entry`; null if known.",
            ),
            Column("is_engineering_works", "boolean", "The worst status is a closure."),
            Column("is_service_closed", "boolean", "TfL reported Service Closed."),
            Column("is_scored", "boolean", "Counts toward the Disruption Index."),
        ),
        order_by=("line_id", "minute_utc"),
    ),
    Dataset(
        name="line_daily",
        source="fct_line_daily",
        description="Declared-view metrics per line and London date, over scheduled minutes.",
        grain="line x London date",
        month_column="london_date",
        columns=(
            _LINE,
            Column("london_date", "date", "London date."),
            Column("week_start", "date", "Monday of the London ISO week."),
            Column("scheduled_minutes", "integer", "Minutes inside scheduled hours."),
            Column("observed_minutes", "integer", "Scheduled minutes with a known status."),
            Column("missing_minutes", "integer", "Scheduled minutes without one."),
            Column("engineering_works_minutes", "integer", "Scheduled minutes under a closure."),
            Column("service_closed_minutes", "integer", "Scheduled minutes at Service Closed."),
            Column("scored_minutes", "integer", "The denominator of every score."),
            Column("good_service_minutes", "integer", "Scored minutes at Good Service."),
            Column("disrupted_minutes", "integer", "Scored minutes with a weight above 0."),
            Column(
                "weighted_disruption_minutes",
                "double",
                "Sum of severity weight x time weight over scored minutes.",
            ),
            Column(
                "time_weighted_scored_minutes",
                "double",
                "Sum of time weight over scored minutes; the Disruption Index's denominator.",
            ),
            Column("incidents", "integer", "Unplanned incidents starting on the date."),
            Column(
                "median_recovery_minutes",
                "double",
                "Median recovery of closed incidents starting on the date.",
            ),
            Column("completeness", "double", "observed_minutes / scheduled_minutes."),
            Column(
                "disruption_index",
                "double",
                "100 x weighted_disruption_minutes / time_weighted_scored_minutes, 0 to 800.",
            ),
            Column("good_service_pct", "double", "Percentage of scored minutes at Good Service."),
            Column("incidents_per_100_hours", "double", "Incidents per 100 scored service hours."),
        ),
        order_by=("london_date", "line_id"),
    ),
    Dataset(
        name="line_weekly",
        source="fct_line_weekly",
        description=(
            "Declared-view metrics and ranks per line and ISO week (Monday to Sunday, "
            "London time), for the weeks that end in the month."
        ),
        grain="line x ISO week",
        month_column="week_end",
        columns=(
            _LINE,
            Column("week_start", "date", "Monday of the London week."),
            Column("week_end", "date", "Sunday of the London week."),
            Column("service_days", "integer", "London dates with scheduled minutes."),
            Column("scheduled_minutes", "integer", "Minutes inside scheduled hours."),
            Column("observed_minutes", "integer", "Scheduled minutes with a known status."),
            Column("missing_minutes", "integer", "Scheduled minutes without one."),
            Column("engineering_works_minutes", "integer", "Scheduled minutes under a closure."),
            Column("service_closed_minutes", "integer", "Scheduled minutes at Service Closed."),
            Column("scored_minutes", "integer", "The denominator of every score."),
            Column("good_service_minutes", "integer", "Scored minutes at Good Service."),
            Column("disrupted_minutes", "integer", "Scored minutes with a weight above 0."),
            Column(
                "weighted_disruption_minutes", "double", "Sum of severity weight x time weight."
            ),
            Column(
                "time_weighted_scored_minutes", "double", "Sum of time weight over scored minutes."
            ),
            Column("incidents", "integer", "Unplanned incidents starting in the week."),
            Column(
                "median_recovery_minutes",
                "double",
                "Median recovery of closed incidents starting in the week.",
            ),
            Column("completeness", "double", "observed_minutes / scheduled_minutes."),
            Column("disruption_index", "double", "Disruption Index for the week, 0 to 800."),
            Column("good_service_pct", "double", "Percentage of scored minutes at Good Service."),
            Column("incidents_per_100_hours", "double", "Incidents per 100 scored service hours."),
            Column(
                "engineering_works_share",
                "double",
                "engineering_works_minutes / scheduled_minutes.",
            ),
            Column("is_ranked", "boolean", "The line has a rank this week."),
            Column(
                "unranked_reason",
                "varchar",
                "`no_scored_minutes` or `engineering_works`; null when ranked.",
            ),
            Column("is_provisional", "boolean", "Completeness under 90%."),
            Column("disruption_rank", "integer", "Rank among ranked lines, 1 = least disrupted."),
            Column(
                "rank_movement", "integer", "Previous rank minus this rank; positive = climbed."
            ),
        ),
        order_by=("week_start", "line_id"),
    ),
)


# ---- Pure parts ----------------------------------------------------------------


def parse_month(text: str) -> date:
    """The first day of a 'YYYY-MM' month."""
    if not _MONTH.fullmatch(text):
        raise OpenDataError(f"month must look like 2026-11, not {text!r}")
    return date(int(text[:4]), int(text[5:]), 1)


def month_end(first: date) -> date:
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return following - timedelta(days=1)


def previous_month(today: date) -> date:
    """The first day of the month before the one ``today`` is in."""
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _identifier(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise OpenDataError(f"not a plain identifier: {name!r}")
    return name


def select_sql(dataset: Dataset, database: str, first: date) -> str:
    """The read-only SELECT behind one dataset for one month, with every column cast."""
    columns = ", ".join(
        f"cast({_identifier(c.name)} as {c.type}) as {c.name}" for c in dataset.columns
    )
    window = f"date '{first.isoformat()}' and date '{month_end(first).isoformat()}'"
    sql = (
        f"select {columns} from {_identifier(database)}.{_identifier(dataset.source)} "
        f"where {_identifier(dataset.month_column)} between {window} "
        f"order by {', '.join(_identifier(c) for c in dataset.order_by)}"
    )
    return check_read_only(sql)


def count_sql(dataset: Dataset, database: str, first: date) -> str:
    window = f"date '{first.isoformat()}' and date '{month_end(first).isoformat()}'"
    return check_read_only(
        f"select count(*) as row_count from {_identifier(database)}.{_identifier(dataset.source)} "
        f"where {_identifier(dataset.month_column)} between {window}"
    )


def completeness_sql(database: str, first: date) -> str:
    window = f"date '{first.isoformat()}' and date '{month_end(first).isoformat()}'"
    return check_read_only(
        "select sum(observed_minutes) as observed_minutes, "
        "sum(scheduled_minutes) as scheduled_minutes, "
        "max(london_date) as last_date "
        f"from {_identifier(database)}.fct_line_daily where london_date between {window}"
    )


def weeks_sql(database: str, first: date) -> str:
    window = f"date '{first.isoformat()}' and date '{month_end(first).isoformat()}'"
    return check_read_only(
        "select count(distinct week_end) as weeks "
        f"from {_identifier(database)}.fct_line_weekly where week_end between {window}"
    )


def sundays_in(first: date) -> int:
    """ISO weeks that end in the month, i.e. its Sundays."""
    last = month_end(first)
    return sum(1 for d in range(first.day, last.day + 1) if first.replace(day=d).isoweekday() == 7)


def freshness_sql(database: str) -> str:
    return check_read_only(
        f"select max(london_ts) as latest_london_ts from {_identifier(database)}.int_line_minute_status"
    )


def check_prefix(prefix: str) -> tuple[str, str]:
    """(bucket, key prefix) of an s3://bucket/prefix/ location, or OpenDataError."""
    match = _S3_PREFIX.fullmatch(prefix)
    if match is None:
        raise OpenDataError(f"not an s3://bucket/prefix/ location: {prefix!r}")
    return match.group(1), match.group(2)


def unload_sql(select: str, destination: str) -> str:
    """UNLOAD of a checked SELECT to a checked, empty prefix, as Snappy Parquet."""
    check_prefix(destination)
    return (
        f"unload ({check_read_only(select)}) to '{destination}' "
        "with (format = 'PARQUET', compression = 'SNAPPY')"
    )


def unload_prefix(output_location: str, month: str, run_id: str) -> str:
    """A fresh prefix under the workgroup's results location; UNLOAD needs an empty one."""
    base = output_location if output_location.endswith("/") else output_location + "/"
    prefix = f"{base}open-data/{month}/{run_id}/"
    check_prefix(prefix)
    return prefix


def published_names(dataset: Dataset, parts: int) -> list[str]:
    """One file is <name>.parquet; several are <name>-000.parquet onwards."""
    if parts < 1:
        raise OpenDataError(f"{dataset.name}: UNLOAD wrote no data files")
    if parts == 1:
        return [f"{dataset.name}.parquet"]
    return [f"{dataset.name}-{i:03d}.parquet" for i in range(parts)]


def is_parquet(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == PARQUET_MAGIC and data[-4:] == PARQUET_MAGIC


@dataclass(frozen=True)
class ExportedFile:
    path: str
    data: bytes


@dataclass(frozen=True)
class ExportedDataset:
    dataset: Dataset
    rows: int
    files: tuple[ExportedFile, ...]


def manifest_document(
    first: date,
    exported: Sequence[ExportedDataset],
    stamp: Stamp,
    *,
    completeness: float | None,
    data_through: datetime | None,
) -> dict[str, Any]:
    month = first.strftime("%Y-%m")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": stamp.generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_window": {
            "start": first.isoformat(),
            "end": month_end(first).isoformat(),
            "timezone": "Europe/London",
            "month": month,
        },
        "completeness": None if completeness is None else round(completeness, 4),
        "git_sha": stamp.git_sha,
        "data_through": None
        if data_through is None
        else data_through.strftime("%Y-%m-%dT%H:%M:%S"),
        "attribution": ATTRIBUTION,
        "licence": {
            "name": LICENCE_NAME,
            "url": LICENCE_URL,
            "source_terms": TFL_TERMS_URL,
            "decision": "D63",
        },
        "datasets": [
            {
                "name": item.dataset.name,
                "description": item.dataset.description,
                "grain": item.dataset.grain,
                "source_model": item.dataset.source,
                "rows": item.rows,
                "files": [
                    {
                        "path": f"open-data/{month}/{f.path}",
                        "bytes": len(f.data),
                        "sha256": hashlib.sha256(f.data).hexdigest(),
                    }
                    for f in item.files
                ],
                "columns": [
                    {"name": c.name, "type": c.type, "description": c.description}
                    for c in item.dataset.columns
                ],
            }
            for item in exported
        ],
    }


README_HEAD = """\
# London rail reliability: open data for {month}

**{attribution}.**

Minute-level declared line status, and the daily and weekly declared-view facts behind the \
Tube League Table, for the London dates {start} to {end}. Built from the TfL Unified API. \
The method and its weights are in `/meta/methodology.json` at the root of this bucket.

## Licence

- Source data: Transport for London, used under TfL's transport data terms ({terms}). \
Keep the attribution "{attribution}" wherever you reuse or redistribute these files.
- The derived figures (weights, scores, ranks) are offered under {licence} ({licence_url}). \
Credit "mind-the-data" alongside the TfL attribution.
- TfL does not endorse this dataset, and it comes with no warranty. Statuses are as TfL \
declared them. A missing minute is missing, never Good Service.

## Notes

- Timestamps are UTC. London dates and time bands are already worked out (Europe/London, \
clock changes included).
- Completeness of the month (observed / scheduled line-minutes): {completeness}. The data \
reaches {data_through} London time.
- Weekly rows are the ISO weeks that end in the month.
- Definitions that are still open decisions can change, and the month may be re-exported. \
`manifest.json` lists the current files with the size and sha256 of each; trust it over a \
directory listing.
- A dataset may be split into numbered parts (`<name>-000.parquet` onwards). Read them \
together, e.g. `read_parquet('line_daily*.parquet')` in DuckDB. Rows are sorted within a \
file, not across parts.
"""


def readme_text(manifest: dict[str, Any]) -> str:
    """README.md for the month, from the manifest so the two always agree."""
    window = manifest["data_window"]
    head = README_HEAD.format(
        month=window["month"],
        start=window["start"],
        end=window["end"],
        attribution=ATTRIBUTION,
        terms=TFL_TERMS_URL,
        licence=LICENCE_NAME,
        licence_url=LICENCE_URL,
        completeness=manifest["completeness"],
        data_through=manifest["data_through"],
    )
    lines = [head]
    for item in manifest["datasets"]:
        lines += [
            f"## {item['name']}",
            "",
            f"{item['description']} Grain: {item['grain']}. Rows: {item['rows']:,}.",
            "",
            "Files: " + ", ".join(f"`{f['path'].rsplit('/', 1)[-1]}`" for f in item["files"]),
            "",
            "| Column | Type | Description |",
            "| --- | --- | --- |",
            *(f"| `{c['name']}` | {c['type']} | {c['description']} |" for c in item["columns"]),
            "",
        ]
    return "\n".join(lines)


# ---- Athena and S3 ---------------------------------------------------------------


def _single_value(
    athena: "AthenaClient", sql: str, *, workgroup: str, database: str
) -> dict[str, str | None]:
    result = run_query(athena, sql, workgroup=workgroup, database=database, max_rows=2)
    if len(result.rows) != 1:
        raise OpenDataError(f"expected one row, got {len(result.rows)}: {sql}")
    return dict(zip(result.columns, result.rows[0], strict=True))


def output_location(athena: "AthenaClient", workgroup: str) -> str:
    config = athena.get_work_group(WorkGroup=workgroup)["WorkGroup"].get("Configuration", {})
    location = config.get("ResultConfiguration", {}).get("OutputLocation")
    if not location:
        raise OpenDataError(f"workgroup {workgroup} has no results location")
    return location


def download_parts(s3: "S3Client", prefix: str) -> list[bytes]:
    """The Parquet files UNLOAD wrote under the prefix, in key order."""
    bucket, key_prefix = check_prefix(prefix)
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_prefix):
        keys += [obj["Key"] for obj in page.get("Contents", []) if obj.get("Size", 0) > 0]
    parts = []
    for key in sorted(keys):
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if not is_parquet(data):
            raise OpenDataError(f"not a Parquet file: s3://{bucket}/{key}")
        parts.append(data)
    return parts


@dataclass
class AthenaOpenData:
    athena: "AthenaClient"
    s3: "S3Client"
    workgroup: str
    database: str
    run_id: str
    poll_s: float = 2.0
    timeout_s: float = 900.0
    sleep: Callable[[float], None] = time.sleep
    bytes_scanned: int = 0
    _results: str | None = None

    def latest_minute_london(self) -> datetime | None:
        cells = _single_value(
            self.athena,
            freshness_sql(self.database),
            workgroup=self.workgroup,
            database=self.database,
        )
        value = cells.get("latest_london_ts")
        return None if not value else datetime.fromisoformat(value.replace(" ", "T"))

    def daily_coverage(self, first: date) -> tuple[float | None, date | None]:
        """Completeness over the month, and the last London date fct_line_daily holds in it."""
        cells = _single_value(
            self.athena,
            completeness_sql(self.database, first),
            workgroup=self.workgroup,
            database=self.database,
        )
        observed, scheduled = cells.get("observed_minutes"), cells.get("scheduled_minutes")
        last = cells.get("last_date")
        last_date = date.fromisoformat(last[:10]) if last else None
        if not scheduled or float(scheduled) == 0:
            return None, last_date
        return float(observed or 0) / float(scheduled), last_date

    def weeks(self, first: date) -> int:
        cells = _single_value(
            self.athena,
            weeks_sql(self.database, first),
            workgroup=self.workgroup,
            database=self.database,
        )
        return int(cells.get("weeks") or 0)

    def export(self, dataset: Dataset, first: date) -> ExportedDataset:
        month = first.strftime("%Y-%m")
        rows = _single_value(
            self.athena,
            count_sql(dataset, self.database, first),
            workgroup=self.workgroup,
            database=self.database,
        )["row_count"]
        if self._results is None:
            self._results = output_location(self.athena, self.workgroup)
        prefix = unload_prefix(self._results, month, self.run_id) + f"{dataset.name}/"
        statement = unload_sql(select_sql(dataset, self.database, first), prefix)
        started = self.athena.start_query_execution(
            QueryString=statement,
            WorkGroup=self.workgroup,
            QueryExecutionContext={"Database": self.database, "Catalog": "AwsDataCatalog"},
        )
        stats = wait_for_query(
            self.athena,
            started["QueryExecutionId"],
            poll_s=self.poll_s,
            timeout_s=self.timeout_s,
            sleep=self.sleep,
        )
        self.bytes_scanned += stats.get("DataScannedInBytes", 0)
        parts = download_parts(self.s3, prefix)
        names = published_names(dataset, len(parts))
        files = tuple(ExportedFile(n, d) for n, d in zip(names, parts, strict=True))
        return ExportedDataset(dataset, int(rows or 0), files)


def export_month(
    source: AthenaOpenData,
    first: date,
    stamp: Stamp,
    *,
    allow_incomplete: bool = False,
) -> tuple[dict[str, Any], list[ExportedDataset]]:
    """Every dataset for the month and its manifest, validated against the contract.

    A month is refused, unless allow_incomplete is set, when any source does
    not reach its end: the minute model's last minute, fct_line_daily's last
    date, and a fct_line_weekly row for every week ending in the month. Each
    mart is checked because a transform run can build the minute model and
    then fail before the marts.
    """
    through = source.latest_minute_london()
    completeness, last_date = source.daily_coverage(first)
    weeks = source.weeks(first)
    last_minute = datetime.combine(month_end(first), datetime.min.time()) + timedelta(
        hours=23, minutes=59
    )
    gaps = []
    if through is None or through < last_minute:
        gaps.append(f"minutes through {through}")
    if last_date != month_end(first):
        gaps.append(f"fct_line_daily through {last_date}")
    if weeks < sundays_in(first):
        gaps.append(f"fct_line_weekly has {weeks} of {sundays_in(first)} weeks")
    if gaps and not allow_incomplete:
        raise OpenDataError(
            f"{first:%Y-%m} is not complete ({'; '.join(gaps)}); "
            "pass --allow-incomplete to export it anyway"
        )
    exported = [source.export(dataset, first) for dataset in DATASETS]
    manifest = manifest_document(
        first,
        exported,
        stamp,
        completeness=completeness,
        data_through=through,
    )
    validate(manifest, "open_data_manifest")
    return manifest, exported


def write_month(
    out_dir: Path, first: date, manifest: dict[str, Any], exported: Sequence[ExportedDataset]
) -> list[Path]:
    month_dir = out_dir / "open-data" / first.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in exported:
        for f in item.files:
            path = month_dir / f.path
            path.write_bytes(f.data)
            written.append(path)
    readme = month_dir / "README.md"
    readme.write_text(readme_text(manifest))
    written.append(readme)
    path = month_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--month", help="London month, e.g. 2026-11 (default: the month before this one)"
    )
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "build" / "open-data")
    parser.add_argument("--workgroup", default=DEFAULT_WORKGROUP)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--publish-workgroup",
        action="store_true",
        help=f"query through {PUBLISH_WORKGROUP}, as the publish role in publish-weekly.yml",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the statements; no AWS calls, no files"
    )
    parser.add_argument(
        "--allow-incomplete", action="store_true", help="export a month the data has not finished"
    )
    args = parser.parse_args(argv)
    try:
        args.workgroup = workgroup_for(args.workgroup, publish=args.publish_workgroup)
        first = (
            parse_month(args.month) if args.month else previous_month(datetime.now(LONDON).date())
        )
    except (ValueError, OpenDataError) as exc:
        parser.error(str(exc))

    if args.dry_run:
        month = first.strftime("%Y-%m")
        destination = f"s3://<{args.workgroup} results>/open-data/{month}/<run id>/"
        print(f"-- open data for {month} through {args.workgroup}, database {args.database}")
        for dataset in DATASETS:
            print(f"-- {dataset.name}: {dataset.grain}, from {dataset.source}")
            print(f"{count_sql(dataset, args.database, first)};")
            select = select_sql(dataset, args.database, first)
            print(f"unload ({select}) to '{destination}{dataset.name}/' with (...);")
        print(f"{freshness_sql(args.database)};")
        print(f"{completeness_sql(args.database, first)};")
        print(f"{weeks_sql(args.database, first)};")
        print(f"would write open-data/{month}/<dataset>.parquet, README.md, manifest.json")
        return 0

    import boto3

    source = AthenaOpenData(
        boto3.client("athena"),
        boto3.client("s3"),
        workgroup=args.workgroup,
        database=args.database,
        run_id=f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
    )
    stamp = Stamp(generated_at=datetime.now(UTC), git_sha=git_sha())
    try:
        manifest, exported = export_month(
            source, first, stamp, allow_incomplete=args.allow_incomplete
        )
    except OpenDataError as exc:
        print(f"open data: {exc}", file=sys.stderr)
        return 1
    for path in write_month(args.out_dir, first, manifest, exported):
        print(f"wrote {path}")
    print(f"scanned {source.bytes_scanned:,} bytes in UNLOAD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
