import gzip
import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from mind_the_data.common.raw import (
    Envelope,
    Feed,
    RequestInfo,
    decode_ndjson_gz,
    encode_ndjson_gz,
    object_key,
)

POLL_ID = UUID("5f0c6a52-0d6b-4f5e-9d38-2b1a8a1c7e11")
# Millisecond precision, as stored in the envelope.
POLLED_AT = datetime(2026, 11, 8, 8, 15, 0, 123000, tzinfo=UTC)


def _envelope(**overrides: object) -> Envelope:
    fields: dict[str, object] = {
        "poll_id": POLL_ID,
        "polled_at": POLLED_AT,
        "feed": Feed.ARRIVALS,
        "request": RequestInfo(path="/StopPoint/940GZZLUOXC/Arrivals"),
        "http_status": 200,
        "latency_ms": 184,
        "payload": [{"vehicleId": "201"}],
    }
    fields.update(overrides)
    return Envelope.model_validate(fields)


def test_object_key_matches_contract() -> None:
    key = object_key(Feed.ARRIVALS, POLLED_AT, POLL_ID)
    assert key == (
        "raw/arrivals/dt=2026-11-08/hour=08/"
        "arrivals_20261108T081500Z_5f0c6a52-0d6b-4f5e-9d38-2b1a8a1c7e11.ndjson.gz"
    )


def test_object_key_partitions_in_utc_not_london() -> None:
    # 00:30 BST on 1 July is 23:30 UTC on 30 June.
    bst = timezone(timedelta(hours=1))
    key = object_key(Feed.LINE_STATUS, datetime(2026, 7, 1, 0, 30, tzinfo=bst), POLL_ID)
    assert key.startswith("raw/line_status/dt=2026-06-30/hour=23/line_status_20260630T233000Z_")


def test_object_key_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="naive"):
        object_key(Feed.LINE_STATUS, datetime(2026, 11, 8, 8, 15), POLL_ID)


def test_envelope_serialises_to_the_documented_shape() -> None:
    line = json.loads(_envelope().model_dump_json())
    assert line == {
        "poll_id": str(POLL_ID),
        "polled_at": "2026-11-08T08:15:00.123Z",
        "feed": "arrivals",
        "request": {"path": "/StopPoint/940GZZLUOXC/Arrivals"},
        "http_status": 200,
        "latency_ms": 184,
        "error": None,
        "payload": [{"vehicleId": "201"}],
    }


def test_failed_request_without_response_is_representable() -> None:
    env = _envelope(http_status=None, latency_ms=45000, error="timeout", payload=None)
    assert not env.ok
    assert json.loads(env.model_dump_json())["http_status"] is None


@pytest.mark.parametrize(("status", "ok"), [(200, True), (204, True), (404, False), (503, False)])
def test_ok_reflects_http_status(status: int, ok: bool) -> None:
    assert _envelope(http_status=status).ok is ok


def test_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate({**_envelope().model_dump(), "app_key": "x"})


def test_ndjson_round_trip_keeps_every_line() -> None:
    envs = [_envelope(), _envelope(request=RequestInfo(path="/StopPoint/X/Arrivals"))]
    data = encode_ndjson_gz(envs)
    text = gzip.decompress(data).decode()
    assert text.count("\n") == 2
    assert decode_ndjson_gz(data) == envs


def test_polled_at_is_stored_to_the_millisecond() -> None:
    env = _envelope(polled_at=datetime(2026, 11, 8, 8, 15, 0, 123999, tzinfo=UTC))
    (decoded,) = decode_ndjson_gz(encode_ndjson_gz([env]))
    assert decoded.polled_at == datetime(2026, 11, 8, 8, 15, 0, 123000, tzinfo=UTC)


def test_ndjson_encoding_is_deterministic() -> None:
    assert encode_ndjson_gz([_envelope()]) == encode_ndjson_gz([_envelope()])
