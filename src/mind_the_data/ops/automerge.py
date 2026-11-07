"""Which pull requests the automerge workflow may merge without a human.

Standard library only: the workflow runs this with the runner's Python from
the default branch's checkout, before any dependency is installed, so a pull
request can never change the rule that judges it.

Usage (reads ``gh pr view --json author,isDraft,isCrossRepository,files,
headRefOid,statusCheckRollup`` output on stdin):

    python -m mind_the_data.ops.automerge --owner OWNER --sha HEAD_SHA
"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any

ALLOWED_PREFIXES = ("transform/models/", "tests/", "docs/")
DENIED_PREFIXES = ("docs/adr/",)
# The automerge job itself is not attached to the pull request, so it never
# appears here; anything else still running or failed blocks the merge.
_OK_OUTCOMES = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def path_allowed(path: str) -> bool:
    if any(path.startswith(p) for p in DENIED_PREFIXES):
        return False
    return any(path.startswith(p) for p in ALLOWED_PREFIXES)


def blocked_paths(paths: Iterable[str]) -> list[str]:
    return sorted(p for p in paths if not path_allowed(p))


def reasons_not_to_merge(pr: dict[str, Any], owner: str, sha: str) -> list[str]:
    """Empty when the pull request may be merged; otherwise why not."""
    reasons: list[str] = []
    author = (pr.get("author") or {}).get("login", "")
    if author.lower() != owner.lower():
        reasons.append(f"author {author!r} is not the repository owner")
    if pr.get("isCrossRepository", True):
        reasons.append("head branch is in another repository")
    if pr.get("isDraft", True):
        reasons.append("pull request is a draft")
    if pr.get("headRefOid") != sha:
        reasons.append("the branch moved after ci ran")
    files: Sequence[dict[str, Any]] = pr.get("files") or []
    if not files:
        reasons.append("no changed files listed")
    # gh lists at most 100 files; a bigger change needs a person anyway.
    if len(files) >= 100:
        reasons.append("too many files to check")
    blocked = blocked_paths(f["path"] for f in files)
    if blocked:
        reasons.append("paths outside the allow-list: " + ", ".join(blocked[:10]))
    for check in pr.get("statusCheckRollup") or []:
        outcome = (check.get("conclusion") or check.get("state") or "").upper()
        if outcome not in _OK_OUTCOMES:
            name = check.get("name") or check.get("context") or "?"
            reasons.append(f"check {name} is {outcome or 'pending'}")
    return reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide whether a PR may be merged")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    reasons = reasons_not_to_merge(json.load(sys.stdin), args.owner, args.sha)
    for reason in reasons:
        print(f"not merging: {reason}")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
