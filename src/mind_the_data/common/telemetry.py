"""Structured JSON logs, with CloudWatch metrics carried in the same line.

Each poll logs one JSON line. That line also uses the CloudWatch Embedded
Metric Format, so CloudWatch turns its counts into metrics (ObjectsWritten,
RequestFailures per Feed) without a separate PutMetricData call.
"""

import json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from mind_the_data.common.clock import utc_now

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        line.update(extra)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def emf_record(
    namespace: str,
    feed_counts: Mapping[str, Mapping[str, int]],
    fields: Mapping[str, Any],
    timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """One EMF document per feed: {feed: {"ObjectsWritten": n, "RequestFailures": m}}.

    EMF allows one set of dimension values per document, so each feed gets its
    own line; ``fields`` (poll id, latency, counts) are repeated on each.
    """
    ts = int((timestamp or utc_now()).timestamp() * 1000)
    docs = []
    for feed, metrics in sorted(feed_counts.items()):
        doc: dict[str, Any] = {
            "_aws": {
                "Timestamp": ts,
                "CloudWatchMetrics": [
                    {
                        "Namespace": namespace,
                        "Dimensions": [["Feed"]],
                        "Metrics": [{"Name": name, "Unit": "Count"} for name in sorted(metrics)],
                    }
                ],
            },
            "Feed": feed,
            **fields,
            **metrics,
        }
        docs.append(doc)
    return docs


def print_emf(docs: list[dict[str, Any]]) -> None:
    # EMF must be a raw JSON line on stdout, not wrapped by the log formatter.
    for doc in docs:
        sys.stdout.write(json.dumps(doc, default=str, separators=(",", ":")) + "\n")
    sys.stdout.flush()
