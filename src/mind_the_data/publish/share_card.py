"""Share cards: the league as a PNG for LinkedIn and X (PLAN 8).

`card_html` is pure and tested directly. `render_png` drives a headless
Chromium through Playwright: the browser Playwright installs
(`uv run playwright install chromium`) or, failing that, a local Google
Chrome. Rendering fails rather than writing a card whose content overflows.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from importlib import resources
from pathlib import Path
from string import Template

from mind_the_data.publish.insight import Insight, lowest_good_service_insight
from mind_the_data.publish.league import League, Standing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardSize:
    name: str
    width: int
    height: int
    columns: int
    row_height: int
    row_size: int
    title_size: int
    insight_size: int


LANDSCAPE = CardSize("1200x675", 1200, 675, 2, 34, 15, 30, 21)
# 20 rows x 35px leave room for a worst-case footer (partial window, four
# lines in every provisional group) under the trains-run insight and caveat.
# The portrait card has no fallback, so an overflow stops the publish.
PORTRAIT = CardSize("1080x1350", 1080, 1350, 1, 35, 20, 44, 28)
SIZES = (LANDSCAPE, PORTRAIT)

# Card text is plain ASCII, so no font on any runner can lack a glyph for it
# (M5 acceptance: no missing glyphs). A test checks the rendered text.
PROVISIONAL_MARK = "*"

# The card travels without its methodology page, so it names the measure,
# its scale and its direction itself. The index rests on TfL's declared
# status, which the title says (wording approved with D19, D22 and D36).
TITLE = "Which lines did TfL report as most disrupted?"
SUBTITLE = (
    "Disruption Index: TfL-reported severity, peak hours weighted, from 0 (Good Service "
    "throughout) to 800 (suspended throughout). Lower is better."
)
GOOD_SERVICE_HEADER = "% time reported Good Service"
INDEX_HEADER = "Disruption Index"
# D22: shown in place of the numbers for a line that ran under half its hours.
NOT_RANKED_WORKS = "Not ranked: engineering works"
METHODOLOGY_NOTE = "Methodology v0.3: some definitions are provisional and may be revised."


class CardOverflow(RuntimeError):
    """The rendered card's content does not fit its size."""


def _template() -> Template:
    text = resources.files("mind_the_data.publish").joinpath("templates", "card.html").read_text()
    return Template(text)


def window_label(start: date, end: date) -> str:
    """'4 to 10 Nov 2026', '28 Oct to 3 Nov 2026', '29 Dec 2026 to 4 Jan 2027'."""
    if start.year != end.year:
        return f"{start:%-d %b %Y} to {end:%-d %b %Y}"
    if start.month != end.month:
        return f"{start:%-d %b} to {end:%-d %b %Y}"
    if start == end:
        return f"{end:%-d %b %Y}"
    return f"{start:%-d} to {end:%-d %b %Y}"


def _fmt_index(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _row(standing: Standing) -> str:
    line = standing.line
    rank = "" if standing.rank is None else str(standing.rank)
    if standing.unranked_reason == "engineering_works":
        # The works are the reason; a provisional mark would only repeat it.
        return (
            '<div class="row unranked">'
            f'<div class="rank">{escape(rank)}</div>'
            f'<div class="name"><span class="chip" style="background:{escape(line.colour)}">'
            f"</span>{escape(line.line_name)}</div>"
            f'<div class="note">{escape(NOT_RANKED_WORKS)}</div>'
            "</div>"
        )
    mark = f' <span class="mark">{PROVISIONAL_MARK}</span>' if standing.is_provisional else ""
    pct = standing.good_service_pct
    width = 0.0 if pct is None else max(0.0, min(100.0, pct))
    css_class = "row unranked" if standing.rank is None else "row"
    return (
        f'<div class="{css_class}">'
        f'<div class="rank">{escape(rank)}</div>'
        f'<div class="name"><span class="chip" style="background:{escape(line.colour)}"></span>'
        f"{escape(line.line_name)}{mark}</div>"
        f'<div class="bar"><span style="width:{width:.1f}%;background:{escape(line.colour)}">'
        "</span></div>"
        f'<div class="num">{_fmt_pct(pct)}</div>'
        f'<div class="num di">{_fmt_index(standing.disruption_index)}</div>'
        "</div>"
    )


def _head() -> str:
    return (
        '<div class="head"><div></div><div>Line</div>'
        f'<div class="gs">{escape(GOOD_SERVICE_HEADER)}</div>'
        f'<div class="num">{escape(INDEX_HEADER)}</div></div>'
    )


def _columns(standings: Sequence[Standing], columns: int) -> list[list[Standing]]:
    per_column = -(-len(standings) // columns)
    return [list(standings[i : i + per_column]) for i in range(0, len(standings), per_column)]


def footer_lines(league: League) -> list[str]:
    notes = []
    completeness = league.completeness
    data = "Data: TfL status feed, read every minute."
    if completeness is not None:
        data += f" {completeness:.1%} of scheduled minutes observed."
    notes.append(data)
    notes.append(
        "Weights: peaks x2, weekends x0.75. Closures and missing data are left out; "
        "lines open under half their hours are not ranked."
    )
    if not league.is_complete:
        through = league.latest_data_london
        notes.append(
            "Partial window: data through "
            + (f"{through:%a %-d %b %H:%M} London time." if through else "an unknown time.")
        )
    groups = [
        (_names(league, "mostly_engineering_works"), "a large share of hours closed for works"),
        (_names(league, "low_completeness"), "under 90% of minutes observed"),
        (_names(league, "no_scheduled_minutes"), "no scheduled service"),
    ]
    parts = [f"{names} ({why})" for names, why in groups if names]
    if parts:
        notes.append(f"{PROVISIONAL_MARK} Provisional: " + "; ".join(parts) + ".")
    notes.append(METHODOLOGY_NOTE)
    return notes


def _names(league: League, code: str, limit: int = 4) -> str:
    """Line names with a provisional reason, or a count when there are many."""
    # A line not ranked for works says so on its row instead.
    listed = [s for s in league.standings if s.unranked_reason != "engineering_works"]
    names = [s.line.line_name for s in listed if any(r.code == code for r in s.provisional_reasons)]
    if len(names) == len(listed) and names:
        return "all lines"
    if len(names) > limit:
        return f"{len(names)} lines"
    return ", ".join(names)


def card_html(league: League, insight: Insight | None, size: CardSize) -> str:
    standings = list(league.standings)
    columns = _columns(standings, size.columns)
    table = "".join(
        '<div class="col">' + _head() + "".join(_row(s) for s in column) + "</div>"
        for column in columns
    )
    insight_text = insight.text if insight is not None else "Weekly reliability, line by line."
    caveat = (
        f'<span class="caveat">{escape(insight.caveat)}</span>'
        if insight is not None and insight.caveat
        else ""
    )
    footer = "".join(f"<p>{escape(n)}</p>" for n in footer_lines(league))
    name_width = 180 if size.columns == 2 else 280
    values = {
        "width": size.width,
        "height": size.height,
        "columns": size.columns,
        "column_gap": 40,
        "pad_x": 40 if size.columns == 2 else 56,
        "pad_y": 22 if size.columns == 2 else 40,
        "pad_y_small": 12 if size.columns == 2 else 24,
        "kicker_size": 14 if size.columns == 2 else 20,
        "title_size": size.title_size,
        "insight_size": size.insight_size,
        "small_size": 12 if size.columns == 2 else 17,
        "row_height": size.row_height,
        "row_size": size.row_size,
        "chip_height": size.row_size + 2,
        "bar_height": 10 if size.columns == 2 else 14,
        "grid": f"22px {name_width}px 1fr 44px 84px"
        if size.columns == 2
        else f"30px {name_width}px 1fr 64px 120px",
        "kicker": escape("London rail league | " + window_label(*_window(league))),
        "title": escape(TITLE),
        "subtitle": escape(SUBTITLE),
        "subtitle_size": 15 if size.columns == 2 else 22,
        "insight": escape(insight_text),
        "caveat": caveat,
        "caveat_size": 13 if size.columns == 2 else 20,
        "table": table,
        "footer": footer,
    }
    return _template().substitute(values)


def _window(league: League) -> tuple[date, date]:
    return league.window.start, league.window.end


# Reports any element whose content is wider or taller than its box, and
# the card itself if it is taller than the viewport.
_OVERFLOW_JS = """
() => {
  const problems = [];
  const card = document.getElementById('card');
  if (card.scrollHeight > window.innerHeight) {
    problems.push(`card ${card.scrollHeight}px tall > ${window.innerHeight}px`);
  }
  const checked = '.name, .num, .note, .head > div, .subtitle, .table, footer, .insight';
  for (const el of document.querySelectorAll(checked)) {
    if (el.scrollWidth > el.clientWidth + 1) {
      problems.push(`${el.className}: "${el.textContent.trim().slice(0, 40)}" is cut off`);
    }
  }
  const table = document.querySelector('.table');
  if (table.scrollHeight > table.clientHeight + 1) {
    problems.push(`table ${table.scrollHeight}px tall > ${table.clientHeight}px`);
  }
  const footer = document.querySelector('footer');
  if (footer.getBoundingClientRect().bottom > window.innerHeight + 0.5) {
    problems.push('footer below the bottom edge');
  }
  return problems;
}
"""


def render_png(html: str, size: CardSize, path: Path, *, channel: str | None = None) -> None:
    """Render one card. Raises CardOverflow if anything does not fit."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
        except PlaywrightError:
            if channel:
                raise
            browser = p.chromium.launch(channel="chrome")
        try:
            page = browser.new_page(
                viewport={"width": size.width, "height": size.height}, device_scale_factor=1
            )
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            problems: list[str] = page.evaluate(_OVERFLOW_JS)
            if problems:
                raise CardOverflow(f"{size.name}: " + "; ".join(problems))
            page.screenshot(path=str(path), full_page=False)
        finally:
            browser.close()


def render_cards(
    league: League,
    insight: Insight | None,
    out_dir: Path,
    stem: str,
    *,
    channel: str | None = None,
) -> list[Path]:
    """Write <stem>.png (1200x675) and <stem>-1080x1350.png; return the paths."""
    paths = []
    for size in SIZES:
        suffix = "" if size is LANDSCAPE else f"-{size.name}"
        path = out_dir / f"{stem}{suffix}.png"
        try:
            render_png(card_html(league, insight, size), size, path, channel=channel)
        except CardOverflow:
            # The landscape card has little spare height. If the caveat is what
            # does not fit (a partial window adds a footer line), lead with the
            # Good Service % insight there rather than drop the caveat.
            if size is not LANDSCAPE or insight is None or not insight.caveat:
                raise
            fallback = lowest_good_service_insight(league)
            # The JSON and the numbers sheet still carry the trains-run text,
            # so say loudly that this card does not match them.
            logger.warning(
                "landscape card fell back to the Good Service insight: caveat did not fit"
            )
            render_png(card_html(league, fallback, size), size, path, channel=channel)
        paths.append(path)
    return paths
