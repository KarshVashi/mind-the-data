"""Check that the published site data is fresh (read-only, no AWS).

    uv run python scripts/ops_published.py --out published.md

Exits 0 when fresh, 3 when stale or unreachable (a crash exits 1), like
ops_report.py.
"""

import argparse
import sys
from pathlib import Path

from mind_the_data.common.clock import utc_now
from mind_the_data.ops.health import render
from mind_the_data.ops.published import fetch_section
from mind_the_data.publish.schemas import SCHEMA_VERSION

MISSED = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write the Markdown here")
    parser.add_argument("--footer", default="")
    args = parser.parse_args(argv)
    now = utc_now()
    section = fetch_section(now, SCHEMA_VERSION)
    report = render(f"Published output at {now:%Y-%m-%d %H:%M} UTC", [section], args.footer)
    if args.out:
        args.out.write_text(report)
    else:
        print(report)
    return MISSED if section.problems else 0


if __name__ == "__main__":
    sys.exit(main())
