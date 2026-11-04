"""Read-only Athena queries against the dev workgroup, for investigation.

Only a single SELECT, WITH, SHOW or DESCRIBE statement is accepted. The check
runs before anything reaches AWS, and the dev workgroup's scan limit still
applies on top of it.
"""

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_athena import AthenaClient
    from mypy_boto3_athena.type_defs import GetQueryResultsOutputTypeDef, RowTypeDef

ALLOWED_FIRST_WORDS = frozenset({"select", "with", "show", "describe"})
# Statements that change data or catalog state, refused anywhere outside quotes.
FORBIDDEN_WORDS = frozenset(
    {
        "alter",
        "call",
        "create",
        "delete",
        "deallocate",
        "drop",
        "execute",
        "grant",
        "insert",
        "merge",
        "msck",
        "optimize",
        "prepare",
        "revoke",
        "truncate",
        "unload",
        "update",
        "vacuum",
    }
)
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


class QueryRefused(ValueError):
    """The SQL is not a single read-only statement."""


_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# Single-quoted strings ('' escapes a quote) and double-quoted identifiers.
_QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_WORD = re.compile(r"[a-z_]+")


def _strip(sql: str) -> str:
    """SQL with comments and quoted text blanked out, lower-cased."""
    without_comments = _COMMENT.sub(" ", sql)
    return _QUOTED.sub(" '' ", without_comments).lower()


def check_read_only(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise QueryRefused."""
    bare = _strip(sql).strip()
    if not bare:
        raise QueryRefused("empty query")
    body = bare.rstrip().removesuffix(";").rstrip()
    if ";" in body:
        raise QueryRefused("only one statement is allowed")
    words = _WORD.findall(body)
    if not words or words[0] not in ALLOWED_FIRST_WORDS:
        first = words[0] if words else "?"
        allowed = ", ".join(sorted(w.upper() for w in ALLOWED_FIRST_WORDS))
        raise QueryRefused(f"{first.upper()} is not allowed; use {allowed}")
    forbidden = sorted(set(words) & FORBIDDEN_WORDS)
    if forbidden:
        raise QueryRefused(f"refusing a query containing {', '.join(forbidden).upper()}")
    return sql.strip().rstrip(";").strip()


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    columns: list[str]
    rows: list[list[str | None]]
    bytes_scanned: int
    elapsed_ms: int


def run_query(
    athena: "AthenaClient",
    sql: str,
    *,
    workgroup: str,
    database: str,
    max_rows: int = 100,
    poll_s: float = 1.0,
    timeout_s: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
) -> QueryResult:
    statement = check_read_only(sql)
    started = athena.start_query_execution(
        QueryString=statement,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database, "Catalog": "AwsDataCatalog"},
    )
    query_id = started["QueryExecutionId"]

    waited = 0.0
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in TERMINAL_STATES:
            break
        if waited >= timeout_s:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError(f"query {query_id} still {state} after {timeout_s:g} s; stopped")
        sleep(poll_s)
        waited += poll_s

    if state != "SUCCEEDED":
        reason = execution["Status"].get("StateChangeReason", "no reason given")
        raise RuntimeError(f"query {query_id} {state}: {reason}")

    stats = execution.get("Statistics", {})
    columns, rows = _results(athena, query_id, max_rows)
    return QueryResult(
        query_id=query_id,
        columns=columns,
        rows=rows,
        bytes_scanned=stats.get("DataScannedInBytes", 0),
        elapsed_ms=stats.get("TotalExecutionTimeInMillis", 0),
    )


def _results(
    athena: "AthenaClient", query_id: str, max_rows: int
) -> tuple[list[str], list[list[str | None]]]:
    columns: list[str] = []
    rows: list[list[str | None]] = []
    first_page = True
    for page in _pages(athena, query_id):
        result_set = page["ResultSet"]
        if first_page:
            columns = [c["Name"] for c in result_set["ResultSetMetadata"]["ColumnInfo"]]
        data = result_set["Rows"]
        # SELECT results repeat the header as the first row of the first page.
        if first_page and data and _cells(data[0]) == columns:
            data = data[1:]
        first_page = False
        for row in data:
            rows.append(_cells(row))
            if len(rows) >= max_rows:
                return columns, rows
    return columns, rows


def _pages(athena: "AthenaClient", query_id: str) -> Iterator["GetQueryResultsOutputTypeDef"]:
    yield from athena.get_paginator("get_query_results").paginate(QueryExecutionId=query_id)


def _cells(row: "RowTypeDef") -> list[str | None]:
    return [cell.get("VarCharValue") for cell in row.get("Data", [])]


def format_table(result: QueryResult) -> str:
    rows = [["" if v is None else v for v in row] for row in result.rows]
    widths = [max([len(col), *(len(r[i]) for r in rows)]) for i, col in enumerate(result.columns)]
    line = " | ".join(c.ljust(w) for c, w in zip(result.columns, widths, strict=True))
    rule = "-+-".join("-" * w for w in widths)
    body = [" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in rows]
    return "\n".join([line, rule, *body])


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"
