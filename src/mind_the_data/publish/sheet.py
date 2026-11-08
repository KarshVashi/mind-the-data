"""The numbers sheet: every figure behind a post, as markdown, for checking by hand."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from mind_the_data.publish.insight import Insight
from mind_the_data.publish.league import (
    CLOSURE_SHARE_PROVISIONAL,
    PROVISIONAL_COMPLETENESS,
    League,
)
from mind_the_data.publish.marts import IncidentRow, LineInfo

LONDON = ZoneInfo("Europe/London")
TOP_INCIDENTS = 5


def _num(value: float | None, fmt: str) -> str:
    return "n/a" if value is None else format(value, fmt)


def london_time(utc: datetime) -> str:
    """Display only: an incident start as London wall-clock time."""
    return utc.replace(tzinfo=UTC).astimezone(LONDON).strftime("%a %-d %b %H:%M")


def numbers_sheet(
    league: League,
    insight: Insight | None,
    incidents: Sequence[IncidentRow],
    lines: Sequence[LineInfo],
    *,
    generated_at: datetime,
    git_sha: str,
    bytes_scanned: int | None = None,
    today_london: datetime | None = None,
) -> str:
    window = league.window
    names = {line.line_id: line.line_name for line in lines}
    out: list[str] = []
    out.append(f"# Numbers sheet: {window.start} to {window.end} (London dates, inclusive)")
    out.append("")
    out.append(f"- Generated: {generated_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC, git {git_sha}")
    out.append(f"- Window: {window.days} days; ISO week: {window.iso_week or 'custom window'}")
    out.append(f"- Completeness (all lines): {_num(league.completeness, '.2%')}")
    if today_london is not None and window.end >= today_london.date():
        out.append(
            f"- **The window is not over**: {window.end} is today or later in London, "
            "so its last day is partial."
        )
    out.append(f"- Open decisions behind these numbers: {', '.join(league.open_decisions)}")
    out.append("- Measured view (Honesty Gap): not measured yet (M4)")
    if bytes_scanned is not None:
        out.append(f"- Athena scanned: {bytes_scanned:,} bytes")
    out.append("")

    out.append("## Insight line")
    out.append("")
    out.append(insight.text if insight else "(none)")
    out.append("")

    out.append("## League (Disruption Index, lower is better)")
    out.append("")
    out.append(
        "| Rank | Line | Disruption Index | Good Service % | Incidents | Median recovery (min) "
        "| Completeness | Closures % | Scored hours | Flags |"
    )
    out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in league.standings:
        flags = "; ".join(
            f"{r.detail}" + (f" ({r.decision})" if r.decision else "")
            for r in s.provisional_reasons
        )
        if s.line.is_low_volume:
            flags = "; ".join(f for f in (flags, "low volume") if f)
        out.append(
            f"| {s.rank if s.rank is not None else '-'} | {s.line.line_name} "
            f"| {_num(s.disruption_index, ',.0f')} | {_num(s.good_service_pct, '.1f')} "
            f"| {s.incidents} | {_num(s.median_recovery_minutes, '.0f')} "
            f"| {_num(s.completeness, '.1%')} | {_num(s.closure_share, '.0%')} "
            f"| {s.scored_minutes / 60:.0f} | {flags} |"
        )
    out.append("")
    out.append(
        f"Provisional: completeness under {PROVISIONAL_COMPLETENESS:.0%} (PLAN 7.4), or at least "
        f"{CLOSURE_SHARE_PROVISIONAL:.0%} of scheduled minutes under closures (D22; threshold "
        "pending as D25). Provisional lines are still ranked (D20)."
    )
    out.append("")

    out.append("## Longest incidents (D18)")
    out.append("")
    out.append("| Line | Started (London) | Duration (min) | Worst status | Open | First reason |")
    out.append("|---|---|---:|---|---|---|")
    longest = sorted(
        (i for i in incidents if window.start <= i.start_london_date <= window.end),
        key=lambda i: (-i.duration_minutes, -i.peak_severity_weight, i.started_at_utc),
    )[:TOP_INCIDENTS]
    for i in longest:
        reason = (i.first_reason or "").replace("|", "/").strip()
        out.append(
            f"| {names.get(i.line_id, i.line_id)} | {london_time(i.started_at_utc)} "
            f"| {i.duration_minutes} | {i.worst_status_description} "
            f"| {'yes' if i.is_open else 'no'} | {reason} |"
        )
    if not longest:
        out.append("| (none) | | | | | |")
    out.append("")
    return "\n".join(out)
