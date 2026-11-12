"""League snapshot for a custom window of London dates: numbers sheet, JSON and cards.

    AWS_PROFILE=mtd-dev uv run python scripts/post_snapshot.py \
        --start 2026-11-04 --end 2026-11-10 --out-dir /path/outside/the/repo

For posts whose window is not an ISO week (the first post runs Wednesday to
Tuesday). Reads the prod marts read-only through the dev Athena workgroup,
sums fct_line_daily over the window with the league's rules, and writes:

    numbers.md                   every figure behind the post, for checking
    league.json                  the league contract, data_window = the custom window
    card.png, card-1080x1350.png the share cards

--record saves the query results, and --recorded replays them without AWS.
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import boto3

from mind_the_data.publish.documents import Stamp, league_document
from mind_the_data.publish.export import DEFAULT_DATABASE, DEFAULT_WORKGROUP, git_sha
from mind_the_data.publish.insight import pick_insight
from mind_the_data.publish.league import Window, league_from_daily
from mind_the_data.publish.marts import AthenaMarts, Marts, RecordedMarts
from mind_the_data.publish.schemas import validate
from mind_the_data.publish.sheet import numbers_sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="London date")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="London date")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workgroup", default=DEFAULT_WORKGROUP)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--record", type=Path, help="also save the query results here")
    source.add_argument("--recorded", type=Path, help="replay saved query results")
    parser.add_argument("--no-cards", action="store_true")
    parser.add_argument("--browser-channel", help="Playwright channel, e.g. chrome")
    args = parser.parse_args(argv)
    if not args.workgroup.endswith("-dev"):
        parser.error("queries run only through a dev workgroup (read-only)")

    window = Window(start=args.start, end=args.end)
    athena: AthenaMarts | None = None
    marts: Marts
    if args.recorded is not None:
        marts = RecordedMarts(args.recorded)
    else:
        athena = AthenaMarts(
            boto3.client("athena"),
            workgroup=args.workgroup,
            database=args.database,
            record_to=args.record,
        )
        marts = athena

    lines = marts.lines()
    daily = marts.daily(window.start, window.end)
    incidents = marts.incidents(window.start, window.end)
    league = league_from_daily(window, lines, daily, incidents, marts.latest_minute_london())
    insight = pick_insight(league)
    stamp = Stamp(generated_at=datetime.now(UTC), git_sha=git_sha())

    document = league_document(league, insight, stamp)
    validate(document, "league")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "league.json").write_text(json.dumps(document, indent=2) + "\n")
    sheet = numbers_sheet(
        league,
        insight,
        incidents,
        lines,
        generated_at=stamp.generated_at,
        git_sha=stamp.git_sha,
        bytes_scanned=athena.bytes_scanned if athena else None,
    )
    (args.out_dir / "numbers.md").write_text(sheet)
    print(f"wrote {args.out_dir / 'numbers.md'}")
    print(f"wrote {args.out_dir / 'league.json'}")
    if not league.is_complete:
        print(
            f"warning: partial window, data through {league.latest_data_london}",
            file=sys.stderr,
        )

    if not args.no_cards:
        from mind_the_data.publish.share_card import render_cards

        for path in render_cards(
            league, insight, args.out_dir, "card", channel=args.browser_channel
        ):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
