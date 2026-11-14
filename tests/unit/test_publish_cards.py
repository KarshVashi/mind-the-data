"""Share cards, insight line, numbers sheet and mart parsing."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from mind_the_data.ops.athena import QueryResult
from mind_the_data.publish import marts as marts_module
from mind_the_data.publish.insight import line_label, pick_insight
from mind_the_data.publish.league import League, MeasuredView, Window, league_from_daily
from mind_the_data.publish.marts import (
    AthenaMarts,
    RecordedMarts,
    load_recording,
    parse_incident,
    parse_weekly,
    queries,
)
from mind_the_data.publish.share_card import (
    LANDSCAPE,
    NOT_RANKED_WORKS,
    PORTRAIT,
    PROVISIONAL_MARK,
    SUBTITLE,
    TITLE,
    CardOverflow,
    card_html,
    footer_lines,
    render_png,
    window_label,
)
from mind_the_data.publish.sheet import london_time, numbers_sheet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "marts"
WINDOW = Window(date(2026, 11, 4), date(2026, 11, 8))


@pytest.fixture
def league() -> League:
    marts = RecordedMarts(FIXTURES)
    return league_from_daily(
        WINDOW,
        marts.lines(),
        marts.daily(WINDOW.start, WINDOW.end),
        marts.incidents(WINDOW.start, WINDOW.end),
        marts.latest_minute_london(),
        marts.monday_morning(WINDOW.start, WINDOW.end),
    )


@pytest.fixture
def partial(league: League) -> League:
    """The same window as if the data stopped on Sunday 8 November at 18:21."""
    return replace(league, is_complete=False, latest_data_london=datetime(2026, 11, 8, 18, 21))


# ---- Insight ------------------------------------------------------------------


def test_declared_insight_skips_provisional_lines(league: League) -> None:
    insight = pick_insight(league)
    assert insight is not None
    # Suffragette (0% Good Service, 95% closed) is not ranked (D22), so District leads.
    assert insight.line_id == "district"
    assert (
        insight.text
        == "The District line showed Good Service for only 33% of its scored service time."
    )


def test_honesty_gap_wins_when_measured(league: League) -> None:
    standings = list(league.standings)
    standings[3] = replace(standings[3], measured=MeasuredView(None, None, 4.0, 0.05, None))
    standings[5] = replace(standings[5], measured=MeasuredView(None, None, 9.0, 0.1, None))
    insight = pick_insight(replace(league, standings=tuple(standings)))
    assert insight is not None and insight.kind == "honesty_gap"
    assert insight.line_id == standings[5].line.line_id
    assert "9 hours" in insight.text


def test_no_insight_when_everything_is_provisional(league: League) -> None:
    flagged = tuple(
        replace(s, provisional_reasons=(*s.provisional_reasons, *_reason()))
        for s in league.standings
    )
    assert pick_insight(replace(league, standings=flagged)) is None


def _reason() -> Any:
    from mind_the_data.publish.league import ProvisionalReason

    return (ProvisionalReason("low_completeness", (), "test"),)


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("Central", "The Central line"),
        ("Elizabeth line", "The Elizabeth line"),
        ("DLR", "The DLR"),
        ("Tram", "Trams"),
        ("Hammersmith & City", "The Hammersmith & City line"),
    ],
)
def test_line_label(name: str, label: str) -> None:
    assert line_label(name) == label


# ---- Card HTML --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "label"),
    [
        (date(2026, 11, 4), date(2026, 11, 10), "4 to 10 Nov 2026"),
        (date(2026, 10, 28), date(2026, 11, 3), "28 Oct to 3 Nov 2026"),
        (date(2026, 12, 28), date(2027, 1, 3), "28 Dec 2026 to 3 Jan 2027"),
        (date(2026, 11, 4), date(2026, 11, 4), "4 Nov 2026"),
    ],
)
def test_window_label(start: date, end: date, label: str) -> None:
    assert window_label(start, end) == label


def test_card_html_has_every_line_and_marks_provisional(league: League) -> None:
    html = card_html(league, pick_insight(league), LANDSCAPE)
    for s in league.standings:
        assert s.line.line_name.replace("&", "&amp;") in html
    # A line not ranked for works carries its own note instead of the mark.
    assert html.count(f'<span class="mark">{PROVISIONAL_MARK}</span>') == sum(
        s.is_provisional and s.unranked_reason != "engineering_works" for s in league.standings
    )
    assert html.count(f'<div class="note">{NOT_RANKED_WORKS}</div>') == 1
    assert "4 to 8 Nov 2026" in html
    assert "$" not in html.replace("$$", "")  # every placeholder filled
    assert 'class="col"' in html and html.count('class="col"') == 2
    assert card_html(league, None, PORTRAIT).count('class="col"') == 1


def test_card_names_the_measure_and_its_direction(league: League) -> None:
    """A card seen on its own says what is ranked, its scale and that lower is better."""
    assert TITLE == "Which lines did TfL report as most disrupted?"
    assert "Disruption Index" in SUBTITLE and "0 (Good Service" in SUBTITLE
    assert "to 800 (suspended" in SUBTITLE
    assert SUBTITLE.endswith("Lower is better.")
    for size in (LANDSCAPE, PORTRAIT):
        html = card_html(league, None, size)
        assert f"<h1>{TITLE}</h1>" in html
        assert f'<p class="subtitle">{SUBTITLE}</p>' in html
        columns = html.count('class="col"')
        assert html.count('<div class="gs">% time reported Good Service</div>') == columns
        assert html.count('<div class="num">Disruption Index</div>') == columns
        assert ">Index<" not in html and ">Good Service<" not in html


def test_card_html_escapes_text(league: League) -> None:
    standings = list(league.standings)
    standings[0] = replace(standings[0], line=replace(standings[0].line, line_name="<b>Bad</b>"))
    html = card_html(replace(league, standings=tuple(standings)), None, LANDSCAPE)
    assert "<b>Bad</b>" not in html and "&lt;b&gt;Bad&lt;/b&gt;" in html


def test_footer_names_closures_and_completeness(league: League) -> None:
    lines = footer_lines(league)
    assert lines[0].endswith("99.2% of scheduled minutes observed.")
    assert any("lines open under half their hours are not ranked" in n for n in lines)
    provisional = next(n for n in lines if n.startswith(PROVISIONAL_MARK))
    # Suffragette is not ranked (D22), so it is not listed as provisional.
    assert "Tram, Mildmay (a large share of hours closed for works)" in provisional
    # When every line is short of data, the footer says so rather than listing 20 names.
    short = tuple(replace(s, provisional_reasons=_reason()) for s in league.standings)
    summary = next(n for n in footer_lines(replace(league, standings=short)) if "Provisional" in n)
    assert "all lines (under 90% of minutes observed)" in summary


def _browser_available() -> bool:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for channel in (None, "chrome"):
            try:
                browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
            except PlaywrightError:
                continue
            browser.close()
            return True
    return False


@pytest.fixture(scope="module")
def browser() -> None:
    # Checked only when a rendering test runs; CI has no browser installed.
    if not _browser_available():
        pytest.skip("no Chromium for Playwright")


def test_cards_render_at_their_sizes(browser: None, league: League, tmp_path: Path) -> None:
    import struct

    insight = pick_insight(league)
    for size in (LANDSCAPE, PORTRAIT):
        path = tmp_path / f"{size.name}.png"
        render_png(card_html(league, insight, size), size, path)
        header = path.read_bytes()[:24]
        assert header[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", header[16:24]) == (size.width, size.height)


def test_overflowing_card_is_refused(browser: None, league: League, tmp_path: Path) -> None:
    cramped = replace(LANDSCAPE, row_height=80)
    with pytest.raises(CardOverflow, match="1200x675"):
        render_png(card_html(league, None, cramped), cramped, tmp_path / "x.png")
    assert not (tmp_path / "x.png").exists()


# ---- Numbers sheet ------------------------------------------------------------------


def test_numbers_sheet(league: League) -> None:
    marts = RecordedMarts(FIXTURES)
    sheet = numbers_sheet(
        league,
        pick_insight(league),
        marts.incidents(WINDOW.start, WINDOW.end),
        marts.lines(),
        generated_at=datetime(2026, 11, 8, 22, 0, tzinfo=UTC),
        git_sha="abc1234",
    )
    assert "| - | Suffragette | 211 | 0.0 |" in sheet
    assert "not ranked: engineering works (D22)" in sheet
    assert "| 19 | Bakerloo | 141 | 47.1 |" in sheet
    assert "Data through: Sat 14 Nov 15:09 (London)" in sheet
    assert "Partial window" not in sheet
    # Bakerloo's Part Suspended on Sunday morning: 1,005 minutes, 3,763 weighted.
    assert "- Worst incident (D48): Bakerloo, Sun 8 Nov 07:15, 1005 min, 3,763" in sheet
    rows = [r for r in sheet.splitlines() if r.startswith("| Bakerloo | Sun 8 Nov 07:15 | 1005")]
    assert len(rows) == 1
    assert "- Wooden Spoon (D48): Bakerloo, rank 19" in sheet
    assert "- Line of the Week (D47): none (custom_window)" in sheet
    assert "(no Monday in the window)" in sheet
    assert "| Suffragette | " in sheet.split("## Engineering works (D50)")[1]
    early = numbers_sheet(
        replace(league, is_complete=False),
        None,
        [],
        marts.lines(),
        generated_at=datetime(2026, 11, 12, tzinfo=UTC),
        git_sha="abc1234",
    )
    assert "**Partial window**" in early and "| (none) |" in early


def test_london_time_is_only_formatted() -> None:
    # The marts carry London time already; the sheet never converts.
    assert london_time(datetime(2026, 7, 4, 8, 49)) == "Sat 4 Jul 08:49"


def test_card_text_is_ascii(league: League) -> None:
    """Every character on the card has a glyph in any font (M5: no missing glyphs)."""
    import re

    for size in (LANDSCAPE, PORTRAIT):
        html = card_html(league, pick_insight(league), size)
        body = html[html.index("<body>") :]
        text = re.sub(r"<[^>]+>", "", body)
        assert text.isascii(), sorted({c for c in text if not c.isascii()})


def test_partial_window_is_said_on_the_card(league: League, partial: League) -> None:
    assert league.is_complete and not partial.is_complete
    assert any(
        n.startswith("Partial window: data through Sun 8 Nov 18:21") for n in footer_lines(partial)
    )
    assert not any("Partial" in n for n in footer_lines(league))


# ---- Mart parsing -------------------------------------------------------------------


def test_parsing_handles_nulls_and_athena_formats() -> None:
    incident = parse_incident(
        {
            "line_id": "bakerloo",
            "started_at": "2026-11-08 07:15:00.000000",
            "started_at_london": "2026-11-08 07:15:00.000000",
            "start_london_date": "2026-11-08",
            "duration_minutes": "667",
            "is_open": "true",
            "recovery_minutes": None,
            "peak_severity_weight": "5.0",
            "severity_weighted_minutes": "3335.0",
            "worst_status_description": "Part Suspended",
            "first_reason": "",
        }
    )
    assert incident.started_at_utc == datetime(2026, 11, 8, 7, 15)
    assert incident.is_open and incident.recovery_minutes is None
    assert incident.first_reason is None

    recording = load_recording(FIXTURES / "weekly.json")
    row = recording.cells()[0]
    with pytest.raises(ValueError, match="completeness"):
        parse_weekly({**row, "completeness": None})
    with pytest.raises(ValueError, match="not a boolean"):
        parse_weekly({**row, "is_provisional": "yes"})


def test_queries_are_read_only_and_windowed() -> None:
    from mind_the_data.ops.athena import check_read_only

    sql = queries("mind_the_data_prod", date(2026, 11, 4), date(2026, 11, 10))
    for statement in sql.values():
        check_read_only(statement)
        assert "mind_the_data_prod." in statement
    assert "between date '2026-11-04' and date '2026-11-10'" in sql["daily"]


def test_athena_marts_records_and_refuses_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    recording = load_recording(FIXTURES / "lines.json")

    def fake_run_query(athena: Any, sql: str, **kwargs: Any) -> QueryResult:
        calls.append({"sql": sql, **kwargs})
        return QueryResult("q1", recording.columns, recording.rows, 1024, 10)

    monkeypatch.setattr(marts_module, "run_query", fake_run_query)
    athena = AthenaMarts(
        object(),  # type: ignore[arg-type]
        workgroup="mind-the-data-dev",
        database="mind_the_data_prod",
        record_to=tmp_path,
    )
    assert len(athena.lines()) == 20
    assert calls[0]["workgroup"] == "mind-the-data-dev"
    assert athena.bytes_scanned == 1024
    assert load_recording(tmp_path / "lines.json") == recording

    small = AthenaMarts(
        object(),  # type: ignore[arg-type]
        workgroup="mind-the-data-dev",
        database="mind_the_data_prod",
        max_rows=20,
    )
    with pytest.raises(RuntimeError, match="max_rows"):
        small.lines()
