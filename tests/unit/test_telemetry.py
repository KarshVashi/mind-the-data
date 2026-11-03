import json
import logging
from datetime import UTC, datetime

import pytest

from mind_the_data.common.telemetry import (
    JsonFormatter,
    configure_logging,
    emf_record,
    print_emf,
)


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.LogRecord("poller", logging.INFO, __file__, 1, "poll done", None, None)
    record.poll_id = "abc"
    record.requests = 52
    line = json.loads(JsonFormatter().format(record))
    assert line.pop("time").endswith("Z")
    assert line == {
        "level": "INFO",
        "logger": "poller",
        "message": "poll done",
        "poll_id": "abc",
        "requests": 52,
    }


def test_emf_one_document_per_feed_with_feed_dimension() -> None:
    docs = emf_record(
        "MindTheData",
        {
            "line_status": {"ObjectsWritten": 1, "RequestFailures": 0},
            "arrivals": {"ObjectsWritten": 1, "RequestFailures": 2},
        },
        {"poll_id": "abc"},
        timestamp=datetime(2026, 11, 8, 8, 15, tzinfo=UTC),
    )
    assert [d["Feed"] for d in docs] == ["arrivals", "line_status"]
    arrivals = docs[0]
    meta = arrivals["_aws"]["CloudWatchMetrics"][0]
    assert meta["Namespace"] == "MindTheData"
    assert meta["Dimensions"] == [["Feed"]]
    assert {m["Name"] for m in meta["Metrics"]} == {"ObjectsWritten", "RequestFailures"}
    assert arrivals["_aws"]["Timestamp"] == 1794125700000
    assert arrivals["RequestFailures"] == 2 and arrivals["poll_id"] == "abc"


def test_print_emf_writes_raw_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    print_emf([{"a": 1}, {"b": 2}])
    out = capsys.readouterr().out.splitlines()
    assert [json.loads(line) for line in out] == [{"a": 1}, {"b": 2}]


def test_emf_rejects_fields_that_would_overwrite_metadata() -> None:
    with pytest.raises(ValueError, match="overwrite"):
        emf_record("MindTheData", {"arrivals": {"ObjectsWritten": 1}}, {"Feed": "x"})
    with pytest.raises(ValueError, match="overwrite"):
        emf_record("MindTheData", {"arrivals": {"ObjectsWritten": 1}}, {"ObjectsWritten": 9})


def test_emf_rejects_non_numeric_metrics() -> None:
    with pytest.raises(TypeError):
        emf_record("MindTheData", {"arrivals": {"ObjectsWritten": True}}, {})


def test_emf_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="naive"):
        emf_record("MindTheData", {"arrivals": {"ObjectsWritten": 1}}, {}, datetime(2026, 1, 1))


def test_configure_logging_silences_request_url_loggers() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING
