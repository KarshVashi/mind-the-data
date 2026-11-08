"""Fail if any GitHub Action is not pinned to a full commit SHA with a version comment.

A tag or branch can be moved to different code after review; a 40-character
commit SHA cannot. The trailing comment (`# v4.2.0`) says which release the
SHA is, so updates can be reviewed. Local actions (`./...`) are exempt, and
Docker images must be pinned by digest.

Usage: python scripts/check_action_pins.py [directory ...]   (default: .github)
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

USES_RE = re.compile(
    r"""^\s*(?:-\s+)?uses:\s*(?P<quote>["']?)(?P<ref>[^"'\s#]+)(?P=quote)(?P<rest>.*)$"""
)
PINNED_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
DOCKER_RE = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
VERSION_COMMENT_RE = re.compile(r"^\s+#\s*v?\d+(\.\d+)*\S*")


@dataclass(frozen=True)
class Problem:
    path: Path
    line: int
    ref: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.ref}: {self.reason}"


def check_line(text: str) -> tuple[str, str] | None:
    """(ref, reason) when the line is a `uses:` that is not pinned properly."""
    match = USES_RE.match(text)
    if match is None:
        return None
    ref, rest = match["ref"], match["rest"]
    if ref.startswith("./"):
        return None
    if ref.startswith("docker://"):
        return None if DOCKER_RE.match(ref) else (ref, "docker image not pinned by sha256 digest")
    if not PINNED_RE.match(ref):
        return ref, "not pinned to a 40-character commit SHA"
    if not VERSION_COMMENT_RE.match(rest):
        return ref, "missing a version comment such as '# v1.2.3'"
    return None


def check_file(path: Path) -> list[Problem]:
    problems = []
    for number, text in enumerate(path.read_text().splitlines(), start=1):
        found = check_line(text)
        if found is not None:
            problems.append(Problem(path, number, *found))
    return problems


def yaml_files(roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        if root.is_file():
            files.add(root)
        else:
            files.update(p for p in root.rglob("*") if p.suffix in (".yml", ".yaml"))
    return sorted(files)


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path(".github")]
    files = yaml_files(roots)
    problems = [p for f in files for p in check_file(f)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"check_action_pins: {len(problems)} unpinned action reference(s)")
        return 1
    print(f"check_action_pins: every action in {len(files)} file(s) is pinned to a commit SHA")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
