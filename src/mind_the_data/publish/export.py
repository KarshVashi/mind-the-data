"""Write the published files (PLAN 8) for one ISO week to a local directory.

    uv run python -m mind_the_data.publish.export --out-dir build/publish
    uv run python -m mind_the_data.publish.export --week 2026-W45 --recorded tests/fixtures/marts
    uv run python -m mind_the_data.publish.export --dry-run

Reads the prod marts read-only through Athena (profile mtd-dev, the dev
workgroup) unless --recorded points at saved query results. publish-weekly.yml
passes --publish-workgroup instead: the publish role (mtd-ci-publish) may
query only through the prod workgroup. Every document
is validated against its schema before anything is written; --dry-run
validates and lists the files without writing them.

Written: league/{iso_week}.json for the week and for every complete week
before it, league/latest.json, lines/{line_id}.json, meta/methodology.json,
meta/index.json and cards/{iso_week}.png plus the 1080x1350 variant.
Each league file carries the week's highlights (Wooden Spoon, Line of the
Week, worst incident), Monday Morning Index and Engineering Works table.
divisions/ and season/ wait for the rest of M6, and open-data/ for M7.
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mind_the_data.publish.documents import (
    Stamp,
    index_document,
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
# The only workgroup the publish role's policy allows (infra/modules/ci_role).
PUBLISH_WORKGROUP = "mind-the-data-prod"


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
    own name only and flagged is_window_complete = false.

    Every complete week with observed data is written too, from the same
    marts and code, so the archive, the line histories and meta/index.json
    always agree (D39). league/latest.json is the newest complete week,
    whichever week was asked for.
    """
    window = Window.for_iso_week(iso_week_of(week_start))
    lines = marts.lines()
    weekly = marts.weekly() if weekly is None else weekly
    latest_minute = marts.latest_minute_london()
    observed_weeks = sorted({w.week_start for w in weekly if w.observed_minutes > 0})
    history_start = min([window.start, *observed_weeks])
    history_end = max([window.end, *(w + timedelta(days=6) for w in observed_weeks)])
    # One query for every week's incidents; each league keeps its own window's.
    incidents = marts.incidents(history_start, history_end)
    monday_morning = marts.monday_morning(history_start, history_end)
    league = league_from_weekly(
        window.start, lines, weekly, incidents, latest_minute, monday_morning
    )
    if not league.is_complete and not allow_incomplete:
        raise IncompleteWeek(
            f"{iso_week_of(window.start)} is not complete (data through "
            f"{league.latest_data_london}); pass --allow-incomplete to write it anyway"
        )

    archive = [
        league
        if start == window.start
        else league_from_weekly(start, lines, weekly, incidents, latest_minute, monday_morning)
        for start in observed_weeks
    ]
    archive = [week for week in archive if week.is_complete]
    if league.is_complete and league not in archive:
        archive.append(league)
    outputs = [
        Output(
            f"league/{iso_week_of(week.window.start)}.json",
            league_document(week, pick_insight(week), stamp),
            "league",
        )
        for week in sorted(archive, key=lambda week: week.window.start)
    ]
    if league not in archive:
        outputs.append(
            Output(
                f"league/{iso_week_of(window.start)}.json",
                league_document(league, pick_insight(league), stamp),
                "league",
            )
        )
    if archive:
        newest = max(archive, key=lambda week: week.window.start)
        outputs.append(
            Output(
                "league/latest.json", league_document(newest, pick_insight(newest), stamp), "league"
            )
        )

    bad_hours = marts.bad_hours(history_start, history_end)
    with_history = []
    for line in lines:
        doc = line_document(line, weekly, bad_hours, stamp)
        if doc is not None:
            outputs.append(Output(f"lines/{line.line_id}.json", doc, "line"))
            with_history.append(line)
    outputs.append(
        Output(
            "meta/methodology.json",
            methodology_document(transform_dir, league, stamp),
            "methodology",
        )
    )
    outputs.append(
        Output(
            "meta/index.json",
            index_document(
                archive,
                with_history,
                stamp,
                fallback_window=window,
                latest_data_london=latest_minute,
            ),
            "index",
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


def workgroup_for(requested: str, *, publish: bool) -> str:
    """The workgroup to query through; the prod workgroup only for the publish role.

    Every statement is a single SELECT either way (run_query). The guard
    keeps local runs, which use the dev identity, off the prod workgroup's
    query history and scan budget.
    """
    if publish:
        return PUBLISH_WORKGROUP
    if not requested.endswith("-dev"):
        raise ValueError("queries run only through a dev workgroup (read-only)")
    return requested


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
    parser.add_argument(
        "--publish-workgroup",
        action="store_true",
        help=f"query through {PUBLISH_WORKGROUP}, as the publish role in publish-weekly.yml",
    )
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
    try:
        args.workgroup = workgroup_for(args.workgroup, publish=args.publish_workgroup)
    except ValueError as exc:
        parser.error(str(exc))

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
    # Upload and the site rebuild are a separate step: mind_the_data.publish.upload.
    return 0


if __name__ == "__main__":
    sys.exit(main())
