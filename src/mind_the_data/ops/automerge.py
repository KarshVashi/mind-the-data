"""Which pull requests the automerge workflow may merge without a human.

Standard library only: the workflow runs this with the runner's Python from
the default branch's checkout, before any dependency is installed, so a pull
request can never change the rule that judges it.

Usage:

    gh pr view N --json author,isDraft,isCrossRepository,headRefOid,statusCheckRollup > pr.json
    gh api --paginate repos/OWNER/REPO/pulls/N/files --jq '.[]' > files.jsonl
    python -m mind_the_data.ops.automerge --owner OWNER --sha HEAD_SHA \
        --pr pr.json --files files.jsonl
"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

ALLOWED_PREFIXES = ("transform/models/", "tests/", "docs/")
DENIED_PREFIXES = ("docs/adr/",)
# The automerge job itself is not attached to the pull request, so it never
# appears here; anything else still running or failed blocks the merge.
_OK_OUTCOMES = {"SUCCESS", "NEUTRAL", "SKIPPED"}
# A change this large needs a person whatever it touches.
MAX_FILES = 300


def path_allowed(path: str) -> bool:
    if any(path.startswith(p) for p in DENIED_PREFIXES):
        return False
    return any(path.startswith(p) for p in ALLOWED_PREFIXES)


def blocked_paths(paths: Iterable[str]) -> list[str]:
    return sorted(p for p in paths if not path_allowed(p))


def reasons_not_to_merge(
    pr: dict[str, Any], files: Sequence[dict[str, Any]], owner: str, sha: str
) -> list[str]:
    """Empty when the pull request may be merged; otherwise why not.

    ``pr`` is ``gh pr view --json``; ``files`` is the REST pull request files
    list, which carries ``previous_filename`` for renames.
    """
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
    if not files:
        reasons.append("no changed files listed")
    if len(files) > MAX_FILES:
        reasons.append("too many files to check")
    # A rename or copy is judged on both ends: moving infra.yml to docs/ deletes
    # a protected file even though its new path is allowed.
    paths = [f.get("filename", "") for f in files]
    paths += [f["previous_filename"] for f in files if f.get("previous_filename")]
    blocked = blocked_paths(paths)
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
    parser.add_argument("--pr", type=Path, required=True, help="gh pr view --json output")
    parser.add_argument(
        "--files", type=Path, required=True, help="pull request files, one JSON object a line"
    )
    args = parser.parse_args(argv)
    pr = json.loads(args.pr.read_text())
    lines = args.files.read_text().splitlines()
    files = [json.loads(line) for line in lines if line.strip()]
    reasons = reasons_not_to_merge(pr, files, args.owner, args.sha)
    for reason in reasons:
        print(f"not merging: {reason}")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
