from typing import Any

import pytest

from mind_the_data.ops.athena import (
    QueryRefused,
    QueryResult,
    check_read_only,
    format_table,
    human_bytes,
    run_query,
)


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "SELECT * FROM mind_the_data_raw.line_status WHERE dt = '2026-11-08';",
        "with x as (select 1 as a) select a from x",
        "show tables in mind_the_data_raw",
        "describe mind_the_data_raw.arrivals",
        "-- a comment\nselect 1",
        "/* block\ncomment */ select 1",
        # Forbidden words inside strings, identifiers or comments are harmless.
        "select 'drop table x; insert' as note",
        'select "update" from t',
        "select 1 -- then drop table t",
        "select 'it''s; fine'",
    ],
)
def test_read_only_statements_pass(sql: str) -> None:
    assert check_read_only(sql)


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("", "empty"),
        ("  -- only a comment", "empty"),
        ("drop table mind_the_data_raw.arrivals", "DROP is not allowed"),
        ("insert into t select 1", "INSERT is not allowed"),
        ("create table t as select 1", "CREATE is not allowed"),
        ("unload (select 1) to 's3://x/' with (format = 'json')", "UNLOAD is not allowed"),
        ("msck repair table t", "MSCK is not allowed"),
        ("select 1; drop table t", "one statement"),
        ("select 1;\n-- trailing\n;", "one statement"),
        ("with x as (select 1) insert into t select * from x", "INSERT"),
        ("explain select 1", "EXPLAIN is not allowed"),
        ("SeLeCt 1; DeLeTe FROM t", "one statement"),
    ],
)
def test_writes_and_multiple_statements_are_refused(sql: str, message: str) -> None:
    with pytest.raises(QueryRefused, match=message):
        check_read_only(sql)


def test_trailing_semicolon_is_dropped_before_sending() -> None:
    assert check_read_only("select 1 ;  ") == "select 1"


class FakeAthena:
    def __init__(self, states: list[str], pages: list[dict[str, Any]], **stats: int) -> None:
        self.states = states
        self.pages = pages
        self.stats = stats
        self.started: dict[str, Any] = {}
        self.stopped = False

    def start_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.started = kwargs
        return {"QueryExecutionId": "q-1"}

    def get_query_execution(self, QueryExecutionId: str) -> dict[str, Any]:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": "boom"},
                "Statistics": self.stats,
            }
        }

    def stop_query_execution(self, QueryExecutionId: str) -> None:
        self.stopped = True

    def get_paginator(self, name: str) -> "FakeAthena":
        assert name == "get_query_results"
        return self

    def paginate(self, QueryExecutionId: str) -> list[dict[str, Any]]:
        return self.pages


def _page(rows: list[list[str | None]], columns: list[str] | None = None) -> dict[str, Any]:
    return {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": c} for c in columns or []]},
            "Rows": [{"Data": [{} if v is None else {"VarCharValue": v} for v in r]} for r in rows],
        }
    }


def test_run_query_sends_to_the_dev_workgroup_and_drops_the_header_row() -> None:
    athena = FakeAthena(
        ["QUEUED", "RUNNING", "SUCCEEDED"],
        [
            _page([["feed", "n"], ["arrivals", "1440"]], ["feed", "n"]),
            _page([["line_status", None]]),
        ],
        DataScannedInBytes=2048,
        TotalExecutionTimeInMillis=812,
    )
    result = run_query(
        athena,  # type: ignore[arg-type]
        "select feed, count(*) as n from t group by 1;",
        workgroup="mind-the-data-dev",
        database="mind_the_data_dev",
        sleep=lambda s: None,
    )
    assert athena.started["WorkGroup"] == "mind-the-data-dev"
    assert athena.started["QueryString"] == "select feed, count(*) as n from t group by 1"
    assert athena.started["QueryExecutionContext"]["Database"] == "mind_the_data_dev"
    assert result.columns == ["feed", "n"]
    assert result.rows == [["arrivals", "1440"], ["line_status", None]]
    assert (result.bytes_scanned, result.elapsed_ms) == (2048, 812)


def test_refused_query_never_reaches_athena() -> None:
    athena = FakeAthena(["SUCCEEDED"], [])
    with pytest.raises(QueryRefused):
        run_query(athena, "drop table t", workgroup="w", database="d")  # type: ignore[arg-type]
    assert athena.started == {}


def test_failed_query_raises_with_reason() -> None:
    athena = FakeAthena(["FAILED"], [])
    with pytest.raises(RuntimeError, match="FAILED: boom"):
        run_query(athena, "select 1", workgroup="w", database="d", sleep=lambda s: None)  # type: ignore[arg-type]


def test_slow_query_is_stopped_at_the_timeout() -> None:
    athena = FakeAthena(["RUNNING"], [])
    with pytest.raises(TimeoutError):
        run_query(
            athena,  # type: ignore[arg-type]
            "select 1",
            workgroup="w",
            database="d",
            poll_s=1,
            timeout_s=3,
            sleep=lambda s: None,
        )
    assert athena.stopped


def test_max_rows_limits_output() -> None:
    athena = FakeAthena(["SUCCEEDED"], [_page([["a"], ["1"], ["2"], ["3"]], ["a"])])
    result = run_query(athena, "select a from t", workgroup="w", database="d", max_rows=2)  # type: ignore[arg-type]
    assert result.rows == [["1"], ["2"]]


def test_format_table_and_bytes() -> None:
    table = format_table(QueryResult("q", ["feed", "n"], [["arrivals", "10"], ["x", None]], 0, 0))
    assert table.splitlines() == [
        "feed     | n ",
        "---------+---",
        "arrivals | 10",
        "x        |   ",
    ]
    assert human_bytes(512) == "512 B"
    assert human_bytes(10 * 1024**3) == "10.0 GB"
