"""Share cards: the league as a PNG for LinkedIn and X (PLAN 8).

`card_html` is pure and tested directly. `render_png` drives a headless
Chromium through Playwright: the browser Playwright installs
(`uv run playwright install chromium`) or, failing that, a local Google
Chrome. Rendering fails rather than writing a card whose content overflows.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from importlib import resources
from pathlib import Path
from string import Template

from mind_the_data.publish.insight import Insight
from mind_the_data.publish.league import League, Standing


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


LANDSCAPE = CardSize("1200x675", 1200, 675, 2, 35, 15, 30, 21)
PORTRAIT = CardSize("1080x1350", 1080, 1350, 1, 40, 20, 44, 28)
SIZES = (LANDSCAPE, PORTRAIT)

PROVISIONAL_MARK = "†"  # dagger


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
        '<div class="head"><div></div><div>Line</div><div>Good Service</div>'
        '<div></div><div class="num">Index</div></div>'
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
        "Disruption Index: severity-weighted disrupted minutes per 100 service hours, "
        "peaks count double; lower is better. Planned closures are left out."
    )
    groups = [
        (_names(league, "mostly_engineering_works"), "a large share of hours closed for works"),
        (_names(league, "low_completeness"), "under 90% of minutes observed"),
        (_names(league, "no_scheduled_minutes"), "no scheduled service"),
    ]
    parts = [f"{names} ({why})" for names, why in groups if names]
    if parts:
        notes.append(f"{PROVISIONAL_MARK} Provisional: " + "; ".join(parts) + ".")
    notes.append("Methodology v0.1: weights and definitions are provisional and may be revised.")
    return notes


def _names(league: League, code: str, limit: int = 4) -> str:
    """Line names with a provisional reason, or a count when there are many."""
    names = [
        s.line.line_name
        for s in league.standings
        if any(r.code == code for r in s.provisional_reasons)
    ]
    if len(names) == len(league.standings) and names:
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
    footer = "".join(f"<p>{escape(n)}</p>" for n in footer_lines(league))
    name_width = 190 if size.columns == 2 else 290
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
        "grid": f"22px {name_width}px 1fr 44px 62px"
        if size.columns == 2
        else f"30px {name_width}px 1fr 64px 90px",
        "kicker": escape("London rail league · " + window_label(*_window(league))),
        "title": escape("How reliable was each line?"),
        "insight": escape(insight_text),
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
  for (const el of document.querySelectorAll('.name, .num, .table, footer, .insight')) {
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
        render_png(card_html(league, insight, size), size, path, channel=channel)
        paths.append(path)
    return paths
