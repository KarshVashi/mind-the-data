from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from mind_the_data.ops.published import (
    check_published,
    expected_iso_week,
    fetch_json,
    published_section,
)

# Monday 2026-11-16: 07:00 UTC is 07:00 London (GMT), before the 06:30 + 2h cutoff.
EARLY_MONDAY = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
LATE_MONDAY = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
WEDNESDAY = datetime(2026, 11, 18, 18, 30, tzinfo=UTC)


def _docs(
    week: str = "2026-W46", generated: str = "2026-11-18T18:12:33Z", version: str = "0.8.0"
) -> tuple[dict[str, Any], dict[str, Any]]:
    index = {"schema_version": version, "generated_at": generated}
    latest = {
        "schema_version": version,
        "generated_at": generated,
        "data_window": {"iso_week": week},
    }
    return index, latest


def test_expected_week_midweek_is_last_complete_week() -> None:
    assert expected_iso_week(WEDNESDAY) == "2026-W46"


def test_expected_week_waits_for_monday_publish() -> None:
    assert expected_iso_week(EARLY_MONDAY) == "2026-W45"
    assert expected_iso_week(LATE_MONDAY) == "2026-W46"


def test_expected_week_uses_london_time_in_summer() -> None:
    # 05:45 UTC on Monday 2026-06-01 is 06:45 BST: still inside the grace.
    assert expected_iso_week(datetime(2026, 6, 1, 5, 45, tzinfo=UTC)) == "2026-W21"
    assert expected_iso_week(datetime(2026, 6, 1, 8, 0, tzinfo=UTC)) == "2026-W22"


def test_expected_week_across_new_year() -> None:
    # Wednesday 2027-01-06: the last complete week is 2026-W53.
    assert expected_iso_week(datetime(2027, 1, 6, 12, tzinfo=UTC)) == "2026-W53"


def test_fresh_output_passes() -> None:
    assert check_published(*_docs(), WEDNESDAY, "0.8.0") == []


def test_minor_schema_difference_passes() -> None:
    assert check_published(*_docs(version="0.9.0"), WEDNESDAY, "0.8.0") == []


def test_old_generated_at_fails() -> None:
    problems = check_published(*_docs(generated="2026-11-09T06:40:00Z"), WEDNESDAY, "0.8.0")
    assert any("days old" in p for p in problems)


def test_missed_monday_publish_fails() -> None:
    problems = check_published(*_docs(week="2026-W45"), LATE_MONDAY, "0.8.0")
    assert problems == ["league/latest.json: ISO week is 2026-W45, expected 2026-W46"]


def test_monday_before_publish_is_not_a_failure() -> None:
    docs = _docs(week="2026-W45", generated="2026-11-10T06:40:00Z")
    assert check_published(*docs, EARLY_MONDAY, "0.8.0") == []


def test_schema_major_mismatch_fails() -> None:
    problems = check_published(*_docs(version="1.0.0"), WEDNESDAY, "0.8.0")
    assert len(problems) == 2
    assert all("major" in p for p in problems)


def test_missing_generated_at_is_a_problem_not_a_pass() -> None:
    index, latest = _docs()
    del latest["generated_at"]
    problems = check_published(index, latest, WEDNESDAY, "0.8.0")
    assert any("unreadable" in p for p in problems)


def test_unreachable_files_fail_the_section() -> None:
    section = published_section(None, None, WEDNESDAY, "0.8.0", "ConnectError: boom")
    assert section.problems


def test_fetch_json_uses_stubbed_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"schema_version": "0.8.0"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_json("https://example.test/x.json", client) == {"schema_version": "0.8.0"}


def test_fetch_json_raises_on_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_json("https://example.test/x.json", client)
