"""Write the published files (PLAN 8) for one ISO week to a local directory.

    uv run python -m mind_the_data.publish.export --out-dir build/publish
    uv run python -m mind_the_data.publish.export --week 2026-W45 --recorded tests/fixtures/marts
    uv run python -m mind_the_data.publish.export --dry-run

Reads the prod marts read-only through Athena (profile mtd-dev, the dev
workgroup) unless --recorded points at saved query results. Every document
is validated against its schema before anything is written; --dry-run
validates and lists the files without writing them.

Written: league/{iso_week}.json, league/latest.json, lines/{line_id}.json,
meta/methodology.json and cards/{iso_week}.png plus the 1080x1350 variant.
divisions/ and season/ wait for the league mechanics in M6, and
open-data/ for M7.
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mind_the_data.publish.documents import (
    Stamp,
    iso_week_of,
    league_document,
    line_document,
    methodology_document,
)
from mind_the_data.publish.insight import pick_insight
from mind_the_data.publish.league import League, Window, league_from_weekly
from mind_the_data.publish.marts import AthenaMarts, Marts, RecordedMarts, WeeklyRow
from mind_the_data.publish.schemas import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKGROUP = "mind-the-data-dev"
DEFAULT_DATABASE = "mind_the_data_prod"


class IncompleteWeek(RuntimeError):
    """The requested week has not finished, or the data does not reach past it."""


@dataclass(frozen=True)
class Output:
    path: str
    document: dict[str, Any]
    schema: str


def git_sha(cwd: Path = REPO_ROOT) -> str:
    """The publishing code's commit, suffixed -dirty if the checkout has changes.

    This is the commit of the export and the seeds methodology.json reads,
    not of the dbt build that made the marts.
    """
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    if not head:
        return "unknown"
    return f"{head}-dirty" if status else head


def latest_complete_week(weekly: Sequence[WeeklyRow]) -> date | None:
    # A finished week before the collector started has rows but nothing observed.
    weeks = [w.week_start for w in weekly if w.is_week_complete and w.observed_minutes]
    return max(weeks, default=None)


def build_outputs(
    marts: Marts,
    week_start: date,
    stamp: Stamp,
    *,
    weekly: Sequence[WeeklyRow] | None = None,
    transform_dir: Path = REPO_ROOT / "transform",
    allow_incomplete: bool = False,
) -> tuple[League, list[Output]]:
    """Every JSON document for the week, validated against its schema.

    A week that has not finished, or that the data does not yet reach past,
    is refused unless allow_incomplete is set; it is then written under its
    own name only and flagged is_window_complete = false. league/latest.json
    is written only for the latest complete week.
    """
    window = Window.for_iso_week(iso_week_of(week_start))
    lines = marts.lines()
    weekly = marts.weekly() if weekly is None else weekly
    incidents = marts.incidents(window.start, window.end)
    league = league_from_weekly(
        window.start, lines, weekly, incidents, marts.latest_minute_london()
    )
    if not league.is_complete and not allow_incomplete:
        raise IncompleteWeek(
            f"{iso_week_of(window.start)} is not complete (data through "
            f"{league.latest_data_london}); pass --allow-incomplete to write it anyway"
        )
    write_latest = league.is_complete and latest_complete_week(weekly) == window.start
    insight = pick_insight(league)

    league_doc = league_document(league, insight, stamp)
    iso_week = iso_week_of(window.start)
    outputs = [Output(f"league/{iso_week}.json", league_doc, "league")]
    if write_latest:
        outputs.append(Output("league/latest.json", league_doc, "league"))

    history_start = min(
        (w.week_start for w in weekly if w.observed_minutes > 0), default=window.start
    )
    bad_hours = marts.bad_hours(history_start, window.end)
    for line in lines:
        doc = line_document(line, weekly, bad_hours, stamp)
        if doc is not None:
            outputs.append(Output(f"lines/{line.line_id}.json", doc, "line"))
    outputs.append(
        Output(
            "meta/methodology.json",
            methodology_document(transform_dir, league, stamp),
            "methodology",
        )
    )
    for output in outputs:
        validate(output.document, output.schema)
    return league, outputs


def write_outputs(out_dir: Path, outputs: Sequence[Output]) -> list[Path]:
    written = []
    for output in outputs:
        path = out_dir / output.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output.document, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def marts_from_args(args: argparse.Namespace) -> Marts:
    if args.recorded is not None:
        return RecordedMarts(args.recorded)
    import boto3

    return AthenaMarts(
        boto3.client("athena"),
        workgroup=args.workgroup,
        database=args.database,
        record_to=args.record,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--week", help="ISO week, e.g. 2026-W45; default: latest complete week")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "build" / "publish")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--recorded", type=Path, help="read saved query results, not Athena")
    source.add_argument("--record", type=Path, help="also save the query results here")
    parser.add_argument("--workgroup", default=DEFAULT_WORKGROUP)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--no-cards", action="store_true", help="skip the PNG share cards")
    parser.add_argument("--browser-channel", help="Playwright channel, e.g. chrome")
    parser.add_argument(
        "--dry-run", action="store_true", help="validate and list the files; write nothing"
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="write a week that has not finished (flagged; never as latest.json)",
    )
    args = parser.parse_args(argv)
    if not args.workgroup.endswith("-dev"):
        parser.error("queries run only through a dev workgroup (read-only)")

    marts = marts_from_args(args)
    weekly = marts.weekly()
    if args.week:
        week_start = Window.for_iso_week(args.week).start
    else:
        latest = latest_complete_week(weekly)
        if latest is None:
            raise SystemExit("no complete week with data in fct_line_weekly yet; pass --week")
        week_start = latest
    stamp = Stamp(generated_at=datetime.now(UTC), git_sha=git_sha())
    try:
        league, outputs = build_outputs(
            marts, week_start, stamp, weekly=weekly, allow_incomplete=args.allow_incomplete
        )
    except IncompleteWeek as exc:
        raise SystemExit(str(exc)) from exc
    iso_week = iso_week_of(week_start)

    if args.dry_run:
        for output in outputs:
            size = len(json.dumps(output.document, indent=2))
            print(f"would write {output.path} ({size:,} bytes, valid {output.schema})")
        if not args.no_cards:
            print(f"would render cards/{iso_week}.png and cards/{iso_week}-1080x1350.png")
        return 0

    for path in write_outputs(args.out_dir, outputs):
        print(f"wrote {path}")
    if not args.no_cards:
        from mind_the_data.publish.share_card import render_cards

        insight = pick_insight(league)
        for path in render_cards(
            league, insight, args.out_dir / "cards", iso_week, channel=args.browser_channel
        ):
            print(f"wrote {path}")
    # Upload to R2 and the Pages deploy hook follow once H12 (Cloudflare) is done.
    return 0


if __name__ == "__main__":
    sys.exit(main())
