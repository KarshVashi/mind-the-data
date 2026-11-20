"""Rank the links whose closure would affect the most passengers (ADR-020).

    AWS_PROFILE=mtd-dev uv run python scripts/analysis/critical_links.py --week 2026-11-09
    uv run python scripts/analysis/critical_links.py --status-csv w46_spells.csv

Typical load per link comes from seed_link_demand (NUMBAT 2025). The week's
disrupted minutes come from rpt_line_disrupted_spells in the dev database
(read-only), or from a CSV of the same columns (`--status-csv`), and are
matched to links through TfL's reason text. The method and its limits are
in src/mind_the_data/demand/network.py and docs/methodology.md. Prints
Markdown tables; nothing is written or published.
"""

import argparse
import csv
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from mind_the_data.demand.network import (
    Graph,
    Link,
    StatusSpell,
    exposure,
    graph,
    hour_shares,
    links_from_seed,
    name_index,
    stops_without,
)

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "transform" / "seeds"

# The service day (NUMBAT's 05:00 to 04:59 traffic day) is worked out in
# dbt, in rpt_line_disrupted_spells, so no London-time logic lives here.
SPELLS_SQL = """
select line_id, status, reason, severity_weight, service_day_type, hour_of_day, minutes
from rpt_line_disrupted_spells
where week_start = date '{week}'
"""
MAX_ROWS = 50_000


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def spells_from_rows(rows: Sequence[Mapping[str, str | None]]) -> list[StatusSpell]:
    return [
        StatusSpell(
            line_id=str(row["line_id"]),
            status=str(row["status"]),
            reason=row["reason"] or None,
            severity_weight=float(str(row["severity_weight"])),
            service_day_type=str(row["service_day_type"]),
            hour_of_day=int(str(row["hour_of_day"])),
            minutes=int(str(row["minutes"])),
        )
        for row in rows
    ]


def spells_from_athena(week: date) -> list[StatusSpell]:
    import boto3

    from mind_the_data.ops.athena import run_query

    result = run_query(
        boto3.client("athena"),
        SPELLS_SQL.format(week=week.isoformat()),
        workgroup=os.environ.get("DBT_ATHENA_WORKGROUP_DEV", "mind-the-data-dev"),
        database=os.environ.get("DBT_ATHENA_SCHEMA_DEV", "mind_the_data_dev"),
        max_rows=MAX_ROWS,
    )
    if len(result.rows) >= MAX_ROWS:
        raise SystemExit(f"{MAX_ROWS} rows returned: the spells may be cut short")
    rows = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
    return spells_from_rows(rows)


def line_graphs(links: Mapping[tuple[str, str, str], Link]) -> dict[str, Graph]:
    by_line: dict[str, list[Link]] = {}
    for link in links.values():
        by_line.setdefault(link.numbat_line, []).append(link)
    return {line: graph(items) for line, items in by_line.items()}


def alternative(stops: int | None) -> str:
    if stops is None:
        return "none: network splits"
    if stops == 1:
        return "parallel line"
    return f"{stops} stops round"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--week", type=date.fromisoformat, default=date(2026, 11, 9))
    parser.add_argument("--status-csv", type=Path, help="disrupted spells instead of Athena")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-detour", type=int, default=4)
    args = parser.parse_args(argv)

    links = {
        key: link
        for key, link in links_from_seed(read_csv(SEEDS / "seed_link_demand.csv")).items()
        if link.loads["mon_thu"] > 0
    }
    network = graph(links.values())
    per_line = line_graphs(links)
    names = name_index(links.values())
    shares = hour_shares(read_csv(SEEDS / "seed_line_demand.csv"))
    line_map: dict[str, dict[str, float]] = {}
    for row in read_csv(SEEDS / "seed_demand_line_map.csv"):
        line_map.setdefault(row["numbat_line"], {})[row["line_id"]] = float(row["demand_share"])

    spells = (
        spells_from_rows(read_csv(args.status_csv))
        if args.status_csv
        else spells_from_athena(args.week)
    )
    total_disrupted = sum(spell.minutes for spell in spells)

    def table(title: str, chosen: Sequence[Link]) -> None:
        print(f"## {title}\n")
        print(
            "| # | Line | Link | Weekday load | AM peak | Without it | "
            f"Disrupted min, week of {args.week} | of which weight 5+ | "
            "Severity x passengers (k) | Line min unplaced |"
        )
        print("|---|---|---|---:|---:|---|---:|---:|---:|---:|")
        for position, link in enumerate(chosen, start=1):
            result = exposure(
                link,
                spells,
                line_map.get(link.numbat_line, {}),
                shares,
                per_line[link.numbat_line],
                names[link.numbat_line],
            )
            print(
                f"| {position} | {link.numbat_line} | {link.a_name} - {link.b_name} | "
                f"{link.loads['mon_thu']:,.0f} | {link.am_peak_load:,.0f} | "
                f"{alternative(detours[link.key])} | {result.covered_minutes:,} | "
                f"{result.heavy_minutes:,} | {result.passenger_weighted / 1000:,.1f} | "
                f"{result.unplaced_minutes:,} |"
            )
        print()

    ranked = sorted(links.values(), key=lambda link: link.loads["mon_thu"], reverse=True)
    detours = {link.key: stops_without(network, link) for link in ranked}
    table(
        f"Top {args.top} links by typical Monday-to-Thursday load (NUMBAT 2025)", ranked[: args.top]
    )
    # Single points of failure: busy links with no other line alongside and
    # no way round within `--min-detour` - 1 stops.
    exposed = [
        link
        for link in ranked
        if detours[link.key] is None or (detours[link.key] or 0) >= args.min_detour
    ]
    table(
        f"Top {args.top} links with no way round in under {args.min_detour} stops",
        exposed[: args.top],
    )

    stations = sorted(
        (
            row
            for row in read_csv(SEEDS / "seed_station_demand.csv")
            if row["weekday_entries_exits"]
        ),
        key=lambda row: float(row["weekday_entries_exits"]),
        reverse=True,
    )
    print(f"## Top {args.top} stations by typical weekday entries plus exits (2025 counts)\n")
    print("| # | Station | Mode | Weekday entries + exits |")
    print("|---|---|---|---:|")
    for position, row in enumerate(stations[: args.top], start=1):
        print(
            f"| {position} | {row['station']} | {row['mode']} | "
            f"{float(row['weekday_entries_exits']):,.0f} |"
        )
    print(
        f"\n{total_disrupted:,} disrupted line-minutes read for the week of {args.week}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
