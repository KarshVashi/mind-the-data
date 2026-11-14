"""The monthly open dataset: SQL, layout, manifest, README and the UNLOAD round trip."""

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import boto3
import jsonschema
import pytest
from moto import mock_aws

from mind_the_data.ops.athena import QueryRefused, check_read_only
from mind_the_data.publish import open_data
from mind_the_data.publish.documents import Stamp
from mind_the_data.publish.open_data import (
    DATASETS,
    AthenaOpenData,
    ExportedDataset,
    ExportedFile,
    OpenDataError,
    check_prefix,
    export_month,
    is_parquet,
    manifest_document,
    month_end,
    parse_month,
    previous_month,
    published_names,
    readme_text,
    select_sql,
    unload_prefix,
    unload_sql,
    write_month,
)
from mind_the_data.publish.schemas import validate
from mind_the_data.publish.upload import PARQUET, plan_uploads

DB = "mind_the_data_prod"
NOV = date(2026, 11, 1)
STAMP = Stamp(generated_at=datetime(2026, 12, 2, 6, 45, tzinfo=UTC), git_sha="abc1234")
RESULTS = "mind-the-data-athena-results-123456789012"
OUTPUT = f"s3://{RESULTS}/prod/"
# The smallest bytes that pass the magic-number check; real files come from Athena.
FAKE_PARQUET = b"PAR1" + b"\x00" * 8 + b"PAR1"


def by_name(name: str) -> open_data.Dataset:
    return next(d for d in DATASETS if d.name == name)


# ---- Months -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "first", "last"),
    [
        ("2026-11", date(2026, 11, 1), date(2026, 11, 30)),
        ("2026-12", date(2026, 12, 1), date(2026, 12, 31)),
        ("2028-02", date(2028, 2, 1), date(2028, 2, 29)),
    ],
)
def test_month_bounds(text: str, first: date, last: date) -> None:
    assert parse_month(text) == first
    assert month_end(first) == last


@pytest.mark.parametrize("text", ["2026-13", "2026-1", "2026-11-01", "11-2026", "2026-11; x"])
def test_bad_months_are_refused(text: str) -> None:
    with pytest.raises(OpenDataError, match="month must look like"):
        parse_month(text)


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 12, 2), date(2026, 11, 1)), (date(2027, 1, 2), date(2026, 12, 1))],
)
def test_previous_month(today: date, expected: date) -> None:
    assert previous_month(today) == expected


# ---- SQL --------------------------------------------------------------------


def test_minute_status_select_casts_every_column_and_bounds_the_london_month() -> None:
    sql = select_sql(by_name("line_minute_status"), DB, NOV)
    assert sql.startswith("select cast(line_id as varchar) as line_id, ")
    assert "cast(minute_utc as timestamp(3)) as minute_utc" in sql
    assert f"from {DB}.int_line_minute_status " in sql
    assert "where london_date between date '2026-11-01' and date '2026-11-30' " in sql
    assert sql.endswith("order by line_id, minute_utc")
    check_read_only(sql)


def test_weekly_rows_are_the_weeks_that_end_in_the_month() -> None:
    sql = select_sql(by_name("line_weekly"), DB, NOV)
    assert "where week_end between date '2026-11-01' and date '2026-11-30'" in sql


def test_the_measured_view_is_not_exported() -> None:
    # D42: unpublished until the owner decides.
    names = {c.name for d in DATASETS for c in d.columns}
    for measured in ("excess_wait_minutes", "honesty_gap_hours", "experienced_rank"):
        assert measured not in names
    assert "trains_run_share" not in names


def test_column_names_are_unique_per_dataset() -> None:
    for dataset in DATASETS:
        names = [c.name for c in dataset.columns]
        assert len(names) == len(set(names)), dataset.name


def test_identifiers_are_checked() -> None:
    with pytest.raises(OpenDataError, match="identifier"):
        select_sql(by_name("line_daily"), "prod; drop table x", NOV)


def test_unload_wraps_a_checked_select_and_a_checked_prefix() -> None:
    select = select_sql(by_name("line_daily"), DB, NOV)
    prefix = f"{OUTPUT}open-data/2026-11/run-1/line_daily/"
    sql = unload_sql(select, prefix)
    assert sql == (
        f"unload ({select}) to '{prefix}' with (format = 'PARQUET', compression = 'SNAPPY')"
    )
    with pytest.raises(OpenDataError):
        unload_sql(select, "s3://bucket/x' with (format='json') --/")
    with pytest.raises(QueryRefused):
        unload_sql("delete from t", prefix)


@pytest.mark.parametrize(
    "prefix", ["s3://bucket", "s3://bucket/", "https://bucket/x/", "s3://b/x", "s3://bucket/../x/"]
)
def test_bad_prefixes_are_refused(prefix: str) -> None:
    with pytest.raises(OpenDataError):
        check_prefix(prefix)


def test_unload_prefix_is_fresh_and_under_the_results_location() -> None:
    assert unload_prefix(OUTPUT, "2026-11", "r1") == f"{OUTPUT}open-data/2026-11/r1/"
    assert unload_prefix(OUTPUT.rstrip("/"), "2026-11", "r1") == f"{OUTPUT}open-data/2026-11/r1/"


def test_published_names() -> None:
    daily = by_name("line_daily")
    assert published_names(daily, 1) == ["line_daily.parquet"]
    assert published_names(daily, 3) == [
        "line_daily-000.parquet",
        "line_daily-001.parquet",
        "line_daily-002.parquet",
    ]
    with pytest.raises(OpenDataError, match="no data files"):
        published_names(daily, 0)


def test_parquet_magic() -> None:
    assert is_parquet(FAKE_PARQUET)
    assert not is_parquet(b"PAR1")
    assert not is_parquet(b"line_id,minute_utc\nvictoria,2026-11-01\n")


# ---- Manifest and README ----------------------------------------------------


def exported_fixture() -> list[ExportedDataset]:
    return [
        ExportedDataset(
            d, rows=10 * (i + 1), files=(ExportedFile(f"{d.name}.parquet", FAKE_PARQUET),)
        )
        for i, d in enumerate(DATASETS)
    ]


def test_manifest_matches_the_contract_and_the_files() -> None:
    manifest = manifest_document(
        NOV,
        exported_fixture(),
        STAMP,
        completeness=0.98765,
        data_through=datetime(2026, 12, 2, 3, 20),
    )
    validate(manifest, "open_data_manifest")
    assert manifest["data_window"] == {
        "start": "2026-11-01",
        "end": "2026-11-30",
        "timezone": "Europe/London",
        "month": "2026-11",
    }
    assert manifest["completeness"] == 0.9877
    assert manifest["attribution"] == "Powered by TfL Open Data"
    first = manifest["datasets"][0]
    assert first["files"] == [
        {
            "path": "open-data/2026-11/line_minute_status.parquet",
            "bytes": len(FAKE_PARQUET),
            "sha256": hashlib.sha256(FAKE_PARQUET).hexdigest(),
        }
    ]
    assert first["rows"] == 10


def test_contract_refuses_a_manifest_without_attribution() -> None:
    manifest = manifest_document(
        NOV, exported_fixture(), STAMP, completeness=1.0, data_through=None
    )
    manifest["attribution"] = "TfL"
    with pytest.raises(jsonschema.ValidationError):
        validate(manifest, "open_data_manifest")


def test_readme_carries_the_attribution_licence_and_every_column() -> None:
    manifest = manifest_document(
        NOV, exported_fixture(), STAMP, completeness=0.99, data_through=None
    )
    text = readme_text(manifest)
    assert "**Powered by TfL Open Data.**" in text
    assert "## Licence" in text
    assert "CC BY 4.0" in text
    assert "never Good Service" in text
    for dataset in DATASETS:
        assert f"## {dataset.name}" in text
        for column in dataset.columns:
            assert f"| `{column.name}` | {column.type} |" in text


# ---- The UNLOAD round trip against fakes --------------------------------------


class FakeAthena:
    """Answers the count, freshness and completeness SELECTs, and 'runs' UNLOAD.

    UNLOAD writes the configured number of Parquet parts (plus Athena's
    zero-byte marker) into the moto bucket, as Athena would.
    """

    def __init__(self, s3: Any, *, latest: str, parts: int = 1) -> None:
        self.s3 = s3
        self.latest = latest
        self.parts = parts
        self.statements: list[str] = []
        self.results: dict[str, tuple[list[str], list[list[str | None]]]] = {}

    def get_work_group(self, WorkGroup: str) -> dict[str, Any]:
        assert WorkGroup == "mind-the-data-prod"
        return {"WorkGroup": {"Configuration": {"ResultConfiguration": {"OutputLocation": OUTPUT}}}}

    def start_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        sql = kwargs["QueryString"]
        assert kwargs["WorkGroup"] == "mind-the-data-prod"
        self.statements.append(sql)
        query_id = f"q{len(self.statements)}"
        if sql.startswith("unload"):
            match = re.search(r"to 's3://([^/]+)/([^']+)'", sql)
            assert match
            bucket, prefix = match.groups()
            self.s3.put_object(Bucket=bucket, Key=f"{prefix}", Body=b"")
            for i in range(self.parts):
                self.s3.put_object(Bucket=bucket, Key=f"{prefix}{query_id}_{i}", Body=FAKE_PARQUET)
        elif "max(london_ts)" in sql:
            self.results[query_id] = (["latest_london_ts"], [[self.latest]])
        elif "sum(observed_minutes)" in sql:
            self.results[query_id] = (["observed_minutes", "scheduled_minutes"], [["990", "1000"]])
        else:
            self.results[query_id] = (["row_count"], [["42"]])
        return {"QueryExecutionId": query_id}

    def get_query_execution(self, QueryExecutionId: str) -> dict[str, Any]:
        return {
            "QueryExecution": {
                "Status": {"State": "SUCCEEDED"},
                "Statistics": {"DataScannedInBytes": 1000},
            }
        }

    def get_paginator(self, name: str) -> Any:
        fake = self

        class Paginator:
            def paginate(self, QueryExecutionId: str) -> Iterator[dict[str, Any]]:
                columns, rows = fake.results[QueryExecutionId]
                header = {"Data": [{"VarCharValue": c} for c in columns]}
                body = [{"Data": [{"VarCharValue": v} for v in row]} for row in rows]
                yield {
                    "ResultSet": {
                        "ResultSetMetadata": {"ColumnInfo": [{"Name": c} for c in columns]},
                        "Rows": [header, *body],
                    }
                }

        return Paginator()


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        client.create_bucket(
            Bucket=RESULTS, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
        )
        yield client


def source(
    s3: Any, *, latest: str = "2026-12-02 03:20:00.000000", parts: int = 1
) -> AthenaOpenData:
    return AthenaOpenData(
        FakeAthena(s3, latest=latest, parts=parts),  # type: ignore[arg-type]
        s3,
        workgroup="mind-the-data-prod",
        database=DB,
        run_id="20261202T064500Z-test",
        sleep=lambda _: None,
    )


def test_export_month_end_to_end(s3: Any, tmp_path: Path) -> None:
    src = source(s3)
    manifest, exported = export_month(src, NOV, STAMP)
    assert [e.dataset.name for e in exported] == [d.name for d in DATASETS]
    assert all(e.rows == 42 for e in exported)
    assert manifest["completeness"] == 0.99
    assert manifest["data_through"] == "2026-12-02T03:20:00"
    assert src.bytes_scanned == 3000
    unloads = [s for s in src.athena.statements if s.startswith("unload")]  # type: ignore[attr-defined]
    assert len(unloads) == 3
    assert all(f"to '{OUTPUT}open-data/2026-11/20261202T064500Z-test/" in s for s in unloads)

    written = write_month(tmp_path, NOV, manifest, exported)
    month = tmp_path / "open-data" / "2026-11"
    assert sorted(p.name for p in written) == [
        "README.md",
        "line_daily.parquet",
        "line_minute_status.parquet",
        "line_weekly.parquet",
        "manifest.json",
    ]
    assert json.loads((month / "manifest.json").read_text()) == manifest

    planned = plan_uploads(tmp_path)
    assert planned[-1].key == "open-data/2026-11/manifest.json"
    assert {p.content_type for p in planned if p.key.endswith(".parquet")} == {PARQUET}


def test_several_parts_are_numbered(s3: Any) -> None:
    manifest, exported = export_month(source(s3, parts=2), NOV, STAMP)
    assert [f.path for f in exported[0].files] == [
        "line_minute_status-000.parquet",
        "line_minute_status-001.parquet",
    ]
    validate(manifest, "open_data_manifest")


def test_an_unfinished_month_is_refused(s3: Any) -> None:
    src = source(s3, latest="2026-11-30 23:58:00.000000")
    with pytest.raises(OpenDataError, match="2026-11 is not complete"):
        export_month(src, NOV, STAMP)
    assert not any(s.startswith("unload") for s in src.athena.statements)  # type: ignore[attr-defined]
    manifest, _ = export_month(
        source(s3, latest="2026-11-30 23:58:00.000000"), NOV, STAMP, allow_incomplete=True
    )
    assert manifest["data_through"] == "2026-11-30T23:58:00"


def test_a_part_that_is_not_parquet_is_refused(s3: Any) -> None:
    src = source(s3)
    s3.put_object(
        Bucket=RESULTS,
        Key="prod/open-data/2026-11/20261202T064500Z-test/line_minute_status/stray.csv",
        Body=b"a,b\n1,2\n",
    )
    with pytest.raises(OpenDataError, match="not a Parquet file"):
        export_month(src, NOV, STAMP)


# ---- CLI ----------------------------------------------------------------------


def test_dry_run_prints_the_statements_without_aws(capsys: pytest.CaptureFixture[str]) -> None:
    assert open_data.main(["--month", "2026-11", "--dry-run", "--publish-workgroup"]) == 0
    out = capsys.readouterr().out
    assert "through mind-the-data-prod" in out
    assert out.count("unload (select ") == 3
    assert "would write open-data/2026-11/" in out


def test_local_runs_stay_on_a_dev_workgroup() -> None:
    with pytest.raises(SystemExit):
        open_data.main(["--month", "2026-11", "--dry-run", "--workgroup", "mind-the-data-prod"])
