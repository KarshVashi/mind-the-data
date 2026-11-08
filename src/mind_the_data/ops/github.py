"""A thin, injectable wrapper around the ``gh`` CLI, and the ops issue sync.

One open issue per label at most: a failing run opens it (or comments on the
open one with the new report), and the first passing run closes it. That
keeps the issue list a direct answer to "is anything wrong right now?".
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

Gh = Callable[[Sequence[str], str | None], str]


class GhError(RuntimeError):
    """``gh`` is missing, not signed in, or the call failed."""


def run_gh(args: Sequence[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(
            ["gh", *args], input=stdin, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh {args[0] if args else ''}: {exc}") from exc
    if done.returncode != 0:
        raise GhError(done.stderr.strip() or f"gh exited {done.returncode}")
    return done.stdout


def gh_json(gh: Gh, args: Sequence[str]) -> Any:
    return json.loads(gh(args, None) or "null")


LABEL_COLOUR = "d93f0b"


def sync_issue(gh: Gh, label: str, title: str, body: str, failing: bool, run_url: str) -> str:
    """Open, update or close the single issue for ``label``. Returns what it did."""
    found = gh_json(gh, ["issue", "list", "--label", label, "--state", "open", "--json", "number"])
    numbers = sorted(int(item["number"]) for item in found or [])
    if failing:
        if numbers:
            gh(["issue", "comment", str(numbers[0]), "--body-file", "-"], body)
            return f"commented on #{numbers[0]}"
        gh(
            [
                "label",
                "create",
                label,
                "--force",
                "--color",
                LABEL_COLOUR,
                "--description",
                "Opened and closed by the ops workflows",
            ],
            None,
        )
        out = gh(["issue", "create", "--title", title, "--label", label, "--body-file", "-"], body)
        return f"opened {out.strip()}"
    if not numbers:
        return "nothing open"
    for number in numbers:
        gh(
            [
                "issue",
                "close",
                str(number),
                "--reason",
                "completed",
                "--comment",
                f"Back on target: {run_url}",
            ],
            None,
        )
    return "closed " + ", ".join(f"#{n}" for n in numbers)
