"""Open, update or close the one ops issue for a label (used by the ops workflows).

    uv run python scripts/ops_issue.py --label ops-health --title "..." \\
        --body-file report.md --status failing --run-url "$RUN_URL"

Needs ``gh`` signed in (GH_TOKEN in Actions) with permission to write issues.
"""

import argparse
import sys
from pathlib import Path

from mind_the_data.ops.github import run_gh, sync_issue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--status", choices=["failing", "passing"], required=True)
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    body = args.body_file.read_text()
    print(sync_issue(run_gh, args.label, args.title, body, args.status == "failing", args.run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
