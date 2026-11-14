"""The numbers sheet: every figure behind a post, as markdown, for checking by hand."""

from collections.abc import Sequence
from datetime import UTC, datetime

from mind_the_data.publish.insight import Insight
from mind_the_data.publish.league import (
    CLOSURE_SHARE_PROVISIONAL,
    MIN_OPEN_SHARE_TO_RANK,
    PROVISIONAL_COMPLETENESS,
    League,
)
from mind_the_data.publish.marts import IncidentRow, LineInfo

TOP_INCIDENTS = 5


def _num(value: float | None, fmt: str) -> str:
    return "n/a" if value is None else format(value, fmt)


def london_time(london: datetime) -> str:
    """A London wall-clock time from the marts (dbt converts; nothing here does)."""
    return london.strftime("%a %-d %b %H:%M")


def numbers_sheet(
    league: League,
    insight: Insight | None,
    incidents: Sequence[IncidentRow],
    lines: Sequence[LineInfo],
    *,
    generated_at: datetime,
    git_sha: str,
    bytes_scanned: int | None = None,
) -> str:
    window = league.window
    names = {line.line_id: line.line_name for line in lines}
    out: list[str] = []
    out.append(f"# Numbers sheet: {window.start} to {window.end} (London dates, inclusive)")
    out.append("")
    out.append(f"- Generated: {generated_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC, git {git_sha}")
    out.append(f"- Window: {window.days} days; ISO week: {window.iso_week or 'custom window'}")
    out.append(f"- Completeness (all lines): {_num(league.completeness, '.2%')}")
    through = league.latest_data_london
    out.append(f"- Data through: {london_time(through) if through else 'unknown'} (London)")
    if not league.is_complete:
        out.append(
            f"- **Partial window**: the data does not yet reach past {window.end}, "
            "so the numbers will change."
        )
    out.append(f"- Open decisions behind these numbers: {', '.join(league.open_decisions)}")
    out.append(
        "- Measured view (Honesty Gap, trains run): not published until the owner settles D42 "
        "(gate: ADR-014, D46)"
    )
    if bytes_scanned is not None:
        out.append(f"- Athena scanned: {bytes_scanned:,} bytes")
    out.append("")

    out.append("## Insight line")
    out.append("")
    out.append(insight.text if insight else "(none)")
    out.append("")

    out.append("## League (Disruption Index, 0 to 800, lower is better)")
    out.append("")
    out.append(
        "| Rank | Line | Disruption Index | Good Service % | Incidents | Median recovery (min) "
        "| Completeness | Closures % | Scored hours | Flags |"
    )
    out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in league.standings:
        flags = "; ".join(
            f"{r.detail}" + (f" ({', '.join(r.decisions)})" if r.decisions else "")
            for r in s.provisional_reasons
        )
        if s.line.is_low_volume:
            flags = "; ".join(f for f in (flags, "low volume") if f)
        if s.unranked_reason == "engineering_works":
            flags = "; ".join(f for f in ("not ranked: engineering works (D22)", flags) if f)
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
        f"{CLOSURE_SHARE_PROVISIONAL:.0%} of scheduled minutes under closures (threshold D35). "
        "Provisional lines are still ranked (D20), but never named in the insight (D38). "
        f"A line open under {MIN_OPEN_SHARE_TO_RANK:.0%} of its scheduled minutes because of "
        "engineering works is not ranked (D22)."
    )
    out.append("")

    out.append("## Highlights (M6)")
    out.append("")
    spoon = league.wooden_spoon
    if spoon is None:
        out.append("- Wooden Spoon: none (no ranked line)")
    else:
        shared = f", shared by {spoon.shared_by} lines" if spoon.shared_by > 1 else ""
        provisional = " (provisional)" if spoon.standing.is_provisional else ""
        out.append(
            f"- Wooden Spoon (D48): {spoon.standing.line.line_name}, rank {spoon.standing.rank}, "
            f"index {_num(spoon.standing.disruption_index, ',.0f')}{provisional}{shared}"
        )
    best = league.line_of_the_week
    if best.line is None:
        out.append(f"- Line of the Week (D47): none ({best.null_reason})")
    else:
        out.append(
            f"- Line of the Week (D47): {best.line.line_name}, index "
            f"{_num(best.previous_disruption_index, ',.0f')} to "
            f"{_num(best.disruption_index, ',.0f')} (down {_num(best.improvement, ',.0f')})"
        )
    worst = league.worst_incident
    if worst is None:
        out.append("- Worst incident (D48): none")
    else:
        out.append(
            f"- Worst incident (D48): {worst.line.line_name}, "
            f"{london_time(worst.incident.started_at_london)}, "
            f"{worst.incident.duration_minutes} min, "
            f"{worst.incident.severity_weighted_minutes:,.0f} severity-weighted minutes, "
            f"{worst.incident.worst_status_description}"
        )
    out.append("")

    out.append("## Monday Morning Index (D49, 07:00 to 10:00)")
    out.append("")
    if not league.monday_morning:
        out.append("(no Monday in the window)")
    else:
        out.append("| Monday | Rank | Line | Index | Good Service % | Completeness | Flags |")
        out.append("|---|---:|---|---:|---:|---:|---|")
        for m in league.monday_morning:
            row = m.row
            flags = "; ".join(
                f
                for f in (
                    "provisional" if row.is_provisional else "",
                    f"not ranked: {row.unranked_reason}" if row.unranked_reason else "",
                )
                if f
            )
            out.append(
                f"| {row.week_start} | {row.monday_morning_rank or '-'} | {m.line.line_name} "
                f"| {_num(row.monday_morning_index, ',.0f')} | {_num(row.good_service_pct, '.1f')} "
                f"| {row.completeness:.1%} | {flags} |"
            )
    out.append("")

    out.append("## Engineering works (D50)")
    out.append("")
    closed = sorted(
        (s for s in league.standings if s.engineering_works_minutes > 0),
        key=lambda s: (-s.engineering_works_minutes, s.line.line_name),
    )
    if not closed:
        out.append("(none)")
    else:
        out.append("| Line | Hours closed | Share of scheduled | Ranked |")
        out.append("|---|---:|---:|---|")
        for s in closed:
            ranked = "no" if s.unranked_reason == "engineering_works" else "yes"
            out.append(
                f"| {s.line.line_name} | {s.engineering_works_hours:.1f} "
                f"| {_num(s.closure_share, '.0%')} | {ranked} |"
            )
    out.append("")

    out.append("## Worst incidents (D18, D48: most severity-weighted minutes)")
    out.append("")
    out.append(
        "| Line | Started (London) | Duration (min) | Severity-weighted min | Worst status "
        "| Open | First reason |"
    )
    out.append("|---|---|---:|---:|---|---|---|")
    longest = sorted(
        (i for i in incidents if window.start <= i.start_london_date <= window.end),
        key=lambda i: (
            -i.severity_weighted_minutes,
            -i.duration_minutes,
            i.started_at_utc,
            i.line_id,
        ),
    )[:TOP_INCIDENTS]
    for i in longest:
        reason = (i.first_reason or "").replace("|", "/").strip()
        out.append(
            f"| {names.get(i.line_id, i.line_id)} | {london_time(i.started_at_london)} "
            f"| {i.duration_minutes} | {i.severity_weighted_minutes:,.0f} "
            f"| {i.worst_status_description} | {'yes' if i.is_open else 'no'} | {reason} |"
        )
    if not longest:
        out.append("| (none) | | | | | | |")
    out.append("")
    return "\n".join(out)
