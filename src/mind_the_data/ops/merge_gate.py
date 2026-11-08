"""Merge gate: decide whether a pull request is low-risk enough to merge
without the owner (ADR-011).

The cost of a change cannot be measured before it is deployed, so the gate
uses stand-ins: which paths changed, which Terraform resource types the PR's
own CI plan touches and how, and whether every check on the head commit
passed. Anything it cannot verify is refused.

This module must import only the standard library, pydantic and yaml.
`make merge` loads this file straight from an export of origin/main, not as
part of the installed package, so an import of `mind_the_data` here would
pull in the working tree's copy and undo that pinning.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequiredCheck(_Strict):
    workflow: str
    name: str
    when: Literal["always", "plan_required"]


class MakefileGuard(_Strict):
    path: str
    block_start: str
    block_end: str
    # Changed lines anywhere in the Makefile that match one of these could
    # alter how the guarded recipe runs, so they count as touching it.
    risky_line_patterns: list[str]


class Forbid(_Strict):
    type_prefixes: list[str]
    actions: list[str]
    # Fixed at true: the loader rejects a policy that tries to turn it off.
    unlisted_types: Literal[True]


class Require(_Strict):
    # Always enforced; these three only make the policy file say so, and the
    # loader rejects an attempt to set them false.
    checks_succeeded: Literal[True]
    not_draft: Literal[True]
    pin_head_sha: Literal[True]
    authors: list[str] = Field(min_length=1)
    base_branch: str
    checks: list[RequiredCheck] = Field(min_length=1)
    plan_artifact: str
    refuse_markers: list[str]


class Policy(_Strict):
    version: int
    protected_paths: list[str] = Field(min_length=1)
    # Exact paths matched with case: `makefile` here must not protect `Makefile`.
    protected_exact_paths: list[str]
    makefile: MakefileGuard
    plan_required_paths: list[str] = Field(min_length=1)
    allowed_resource_types: list[str]
    forbid: Forbid
    require: Require


def load_policy(path: Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text()))


def glob_regex(pattern: str) -> re.Pattern[str]:
    """`**` crosses directories (`**/` may match none), `*` and `?` do not.

    Matching ignores case: protecting a path spelt differently is the safe
    side of the mistake.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


def matching(paths: Iterable[str], patterns: Sequence[str]) -> list[str]:
    regexes = [glob_regex(p) for p in patterns]
    return sorted({p for p in paths if any(r.fullmatch(p) for r in regexes)})


@dataclass(frozen=True)
class Check:
    workflow: str
    name: str
    status: str  # COMPLETED, IN_PROGRESS, QUEUED, ... (status contexts: their state)
    conclusion: str  # SUCCESS, FAILURE, SKIPPED, ... or "" while pending
    run_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.workflow} / {self.name}" if self.workflow else self.name


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str
    is_draft: bool
    author: str
    state: str
    base_ref: str
    title: str
    body: str
    commit_messages: tuple[str, ...]
    files: tuple[str, ...]
    checks: tuple[Check, ...]


_RUN_ID = re.compile(r"/actions/runs/(\d+)")


def parse_pr(data: dict[str, Any]) -> PullRequest:
    """Build a PullRequest from `gh pr view --json` output."""
    checks = []
    for item in data.get("statusCheckRollup") or []:
        if item.get("__typename") == "StatusContext":
            state = str(item.get("state") or "")
            checks.append(
                Check(
                    workflow="",
                    name=str(item.get("context") or ""),
                    status="COMPLETED" if state not in ("PENDING", "EXPECTED") else state,
                    conclusion=state if state not in ("PENDING", "EXPECTED") else "",
                )
            )
        else:
            match = _RUN_ID.search(str(item.get("detailsUrl") or ""))
            checks.append(
                Check(
                    workflow=str(item.get("workflowName") or ""),
                    name=str(item.get("name") or ""),
                    status=str(item.get("status") or ""),
                    conclusion=str(item.get("conclusion") or ""),
                    run_id=match.group(1) if match else None,
                )
            )
    commits = tuple(
        f"{c.get('messageHeadline') or ''}\n{c.get('messageBody') or ''}"
        for c in data.get("commits") or []
    )
    return PullRequest(
        number=int(data["number"]),
        head_sha=str(data["headRefOid"]),
        is_draft=bool(data["isDraft"]),
        author=str((data.get("author") or {}).get("login") or ""),
        state=str(data.get("state") or ""),
        base_ref=str(data.get("baseRefName") or ""),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        commit_messages=commits,
        files=tuple(str(f["path"]) for f in data.get("files") or []),
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class MakefileDiff:
    """The Makefile at the merge base and at the head, and the diff's changed lines."""

    base: str
    head: str
    changed_lines: tuple[str, ...]


@dataclass(frozen=True)
class PlanChange:
    address: str
    mode: str
    type: str
    actions: tuple[str, ...]
    importing: bool


def parse_plan_changes(data: Any) -> list[PlanChange]:
    """Read the `plan-changes` artifact (see the infra workflow's plan job)."""
    if not isinstance(data, list):
        raise TypeError("plan changes must be a JSON list")
    changes = []
    for item in data:
        if not isinstance(item, dict):
            raise TypeError("plan change entries must be objects")
        actions = item.get("actions")
        if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
            raise ValueError(f"{item.get('address')}: actions must be a list of strings")
        for key in ("address", "mode", "type"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValueError(f"plan change entry without {key}")
        if not isinstance(item.get("importing"), bool):
            raise TypeError(f"{item['address']}: importing must be true or false")
        changes.append(
            PlanChange(
                address=item["address"],
                mode=item["mode"],
                type=item["type"],
                actions=tuple(actions),
                importing=item["importing"],
            )
        )
    return changes


@dataclass
class Verdict:
    passed: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.refused

    def check(self, ok: bool, good: str, bad: str) -> None:
        (self.passed if ok else self.refused).append(good if ok else bad)


def _block(text: str, guard: MakefileGuard) -> str | None:
    """The marked block's text, "" when there are no markers, None when malformed."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == guard.block_start]
    ends = [i for i, line in enumerate(lines) if line.strip() == guard.block_end]
    if not starts and not ends:
        return ""
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return "\n".join(lines[starts[0] : ends[0] + 1])


def makefile_touches_gate(diff: MakefileDiff, guard: MakefileGuard) -> str | None:
    """Why a Makefile change counts as touching the merge targets, or None.

    Compared at the merge base, not main: a branch made before the gate
    existed has no block on either side, and merging it keeps main's block.
    """
    head_block = _block(diff.head, guard)
    if head_block is None:
        return "the merge gate block markers are malformed at the head"
    if _block(diff.base, guard) != head_block:
        return "the merge gate block was added, removed or changed"
    risky = [re.compile(p, re.IGNORECASE) for p in guard.risky_line_patterns]
    for line in diff.changed_lines:
        if any(r.search(line) for r in risky):
            return f"a changed line could alter the merge recipe: {line.strip()!r}"
    return None


def evaluate_plan(policy: Policy, changes: Sequence[PlanChange]) -> list[str]:
    """Reasons to refuse the plan; empty when every change is allowed."""
    reasons = []
    allowed_types = set(policy.allowed_resource_types)
    for change in changes:
        actions = set(change.actions)
        # An import plans as no-op but brings a resource under this state.
        if change.importing:
            reasons.append(f"{change.address}: imports are not allowed")
            continue
        if actions == {"no-op"} or (change.mode == "data" and actions == {"read"}):
            continue
        if forbidden := actions & set(policy.forbid.actions):
            reasons.append(f"{change.address}: {'/'.join(sorted(forbidden))} is forbidden")
            continue
        if not actions <= {"create", "update"}:
            reasons.append(f"{change.address}: unexpected actions {sorted(actions)}")
            continue
        if any(change.type.startswith(p) for p in policy.forbid.type_prefixes):
            reasons.append(f"{change.address}: {change.type} is forbidden")
        elif change.mode != "managed":
            reasons.append(f"{change.address}: unexpected mode {change.mode}")
        elif change.type not in allowed_types:
            reasons.append(f"{change.address}: {change.type} is not an allowed type")
    return reasons


def needs_plan(policy: Policy, paths: Iterable[str]) -> list[str]:
    return matching(paths, policy.plan_required_paths)


def evaluate(
    policy: Policy,
    pr: PullRequest,
    changed_paths: Sequence[str],
    makefile: MakefileDiff | None,
    plan: Sequence[PlanChange] | None,
    plan_error: str | None = None,
) -> Verdict:
    """Apply the policy to facts already gathered. Pure, so tests need no network."""
    v = Verdict()
    req = policy.require

    v.check(pr.state == "OPEN", "pull request is open", f"pull request is {pr.state}")
    v.check(
        pr.base_ref == req.base_branch,
        f"base branch is {req.base_branch}",
        f"base branch is {pr.base_ref!r}, not {req.base_branch}",
    )
    v.check(not pr.is_draft, "not a draft", "pull request is a draft")
    v.check(
        pr.author in req.authors,
        f"author {pr.author} is allowed",
        f"author {pr.author!r} is not in the allowlist",
    )
    texts = (pr.title, pr.body, *pr.commit_messages)
    marked = [m for m in req.refuse_markers if any(m.lower() in t.lower() for t in texts)]
    v.check(
        not marked,
        "no deploy opt-in markers in the title, body or commits",
        f"{', '.join(marked)} appears in the title, body or a commit message",
    )

    v.check(bool(changed_paths), "changed files listed", "no changed files could be listed")
    protected = sorted(
        set(matching(changed_paths, policy.protected_paths))
        | {p for p in changed_paths if p in policy.protected_exact_paths}
    )
    v.check(
        not protected,
        "no protected paths changed",
        f"protected paths changed: {', '.join(protected)}",
    )
    if policy.makefile.path in changed_paths:
        if makefile is None:
            v.refused.append(f"{policy.makefile.path} changed and its diff could not be read")
        else:
            why = makefile_touches_gate(makefile, policy.makefile)
            v.check(
                why is None,
                f"{policy.makefile.path} changed outside the merge targets",
                f"{policy.makefile.path}: {why}",
            )

    plan_paths = needs_plan(policy, changed_paths)
    _evaluate_checks(v, req, pr, plan_required=bool(plan_paths))

    if plan_paths:
        if plan is None:
            v.refused.append(
                f"deployable paths changed ({', '.join(plan_paths[:3])}"
                f"{', ...' if len(plan_paths) > 3 else ''}) but there is no "
                f"{req.plan_artifact} evidence for {pr.head_sha[:12]}: "
                f"{plan_error or 'not found'}"
            )
        else:
            reasons = evaluate_plan(policy, plan)
            changing = [c for c in plan if set(c.actions) != {"no-op"} and c.mode == "managed"]
            v.check(
                not reasons,
                f"CI plan for the head commit: {len(changing)} resource change(s), all allowed",
                "CI plan refused: " + "; ".join(reasons),
            )
    else:
        v.passed.append("no deployable paths changed, so no plan is needed")
    return v


_OK_SKIPPABLE = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _evaluate_checks(v: Verdict, req: Require, pr: PullRequest, *, plan_required: bool) -> None:
    if not pr.checks:
        v.refused.append("no checks have run on the head commit")
        return
    required = [c for c in req.checks if c.when == "always" or plan_required]
    for rc in required:
        found = [c for c in pr.checks if c.workflow == rc.workflow and c.name == rc.name]
        ok = bool(found) and all(
            c.status == "COMPLETED" and c.conclusion == "SUCCESS" for c in found
        )
        state = ", ".join(f"{c.status}/{c.conclusion or '-'}" for c in found) or "missing"
        v.check(
            ok,
            f"required check {rc.workflow} / {rc.name} succeeded",
            f"required check {rc.workflow} / {rc.name} has not succeeded ({state})",
        )
    bad = [
        f"{c.label} ({c.status}/{c.conclusion or '-'})"
        for c in pr.checks
        if c.status != "COMPLETED" or c.conclusion not in _OK_SKIPPABLE
    ]
    v.check(
        not bad,
        f"all {len(pr.checks)} check(s) on the head commit finished cleanly",
        f"checks not passed: {', '.join(bad)}",
    )


def plan_run_id(policy: Policy, pr: PullRequest) -> str | None:
    for rc in policy.require.checks:
        if rc.when == "plan_required":
            for c in pr.checks:
                if c.workflow == rc.workflow and c.name == rc.name and c.run_id:
                    return c.run_id
    return None


def format_verdict(pr: PullRequest, policy: Policy, verdict: Verdict, source: str) -> str:
    lines = [
        f"merge gate: PR #{pr.number} {pr.title!r}",
        f"head {pr.head_sha} (pinned); policy v{policy.version} from {source}",
    ]
    lines += [f"  ok      {r}" for r in verdict.passed]
    lines += [f"  REFUSE  {r}" for r in verdict.refused]
    lines.append(
        "verdict: ALLOW (low risk, may be merged without the owner)"
        if verdict.allowed
        else "verdict: REFUSE (needs the owner)"
    )
    return "\n".join(lines)


Runner = Callable[[Sequence[str]], str]

PR_FIELDS = (
    "number,files,headRefOid,isDraft,author,statusCheckRollup,state,baseRefName,title,body,commits"
)


def run(argv: Sequence[str]) -> str:
    return subprocess.run(list(argv), check=True, capture_output=True, text=True).stdout


def changed_lines(diff_text: str) -> tuple[str, ...]:
    return tuple(
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


@dataclass(frozen=True)
class Facts:
    pr: PullRequest
    changed_paths: tuple[str, ...]
    makefile: MakefileDiff | None
    plan: list[PlanChange] | None
    plan_error: str | None


def gather(policy: Policy, number: int, runner: Runner = run) -> Facts:
    pr = parse_pr(json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS])))
    sha = pr.head_sha
    branch = policy.require.base_branch
    # The full ref, so a tag or local branch named origin/main cannot stand in.
    main_ref = f"refs/remotes/origin/{branch}"
    runner(["git", "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{branch}:{main_ref}"])
    runner(["git", "fetch", "--quiet", "--no-tags", "origin", sha])
    base = runner(["git", "merge-base", main_ref, sha]).strip()
    # The git diff adds the old side of renames, which the GitHub file list
    # omits, and is not capped at 100 files.
    diff_names = runner(["git", "diff", "--name-only", "--no-renames", "-z", base, sha])
    paths = tuple(sorted(set(pr.files) | {p for p in diff_names.split("\0") if p}))
    # gh lists at most 100 commits; git log has them all for the marker scan.
    log = runner(["git", "log", "--format=%B%x00", f"{base}..{sha}"])
    pr = replace(pr, commit_messages=pr.commit_messages + tuple(log.split("\0")))

    makefile = None
    mk = policy.makefile.path
    if mk in paths:
        try:
            makefile = MakefileDiff(
                base=_show(runner, base, mk),
                head=_show(runner, sha, mk),
                changed_lines=changed_lines(
                    runner(["git", "diff", "--no-renames", "-U0", base, sha, "--", mk])
                ),
            )
        except subprocess.CalledProcessError:
            makefile = None

    plan, plan_error = None, None
    if needs_plan(policy, paths):
        plan, plan_error = _download_plan(policy, pr, runner)
    return Facts(pr, paths, makefile, plan, plan_error)


def _show(runner: Runner, rev: str, path: str) -> str:
    try:
        return runner(["git", "show", f"{rev}:{path}"])
    except subprocess.CalledProcessError:
        return ""  # absent on that side


def _download_plan(
    policy: Policy, pr: PullRequest, runner: Runner
) -> tuple[list[PlanChange] | None, str | None]:
    run_id = plan_run_id(policy, pr)
    if run_id is None:
        return None, "no plan run on the head commit"
    try:
        run_info = json.loads(
            runner(["gh", "run", "view", run_id, "--json", "headSha,event,workflowName"])
        )
        if run_info.get("headSha") != pr.head_sha:
            return None, f"plan run {run_id} is for {run_info.get('headSha')}, not the head"
        # A manual run plans the branch alone, not its merge with main.
        if run_info.get("event") != "pull_request" or run_info.get("workflowName") != "infra":
            return None, f"plan run {run_id} is not an infra pull_request run"
        with tempfile.TemporaryDirectory() as tmp:
            name = policy.require.plan_artifact
            runner(["gh", "run", "download", run_id, "-n", name, "-D", tmp])
            data = json.loads((Path(tmp) / f"{name}.json").read_text())
        return parse_plan_changes(data), None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        return None, f"run {run_id}: {' '.join(exc.cmd[:3])} failed: {' '.join(detail[-1:])}"
    except (OSError, TypeError, ValueError) as exc:
        return None, f"run {run_id}: unreadable {policy.require.plan_artifact}: {exc}"


def merge_argv(pr: PullRequest) -> list[str]:
    return [
        "gh", "pr", "merge", str(pr.number), "--squash",
        "--match-head-commit", pr.head_sha, "--delete-branch=false",
    ]  # fmt: skip


def main(
    argv: Sequence[str] | None = None, runner: Runner = run, policy_path: Path | None = None
) -> int:
    parser = argparse.ArgumentParser(
        description="Merge gate for low-risk pull requests (ADR-011).", allow_abbrev=False
    )
    parser.add_argument("pr", type=int, help="pull request number")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="merge", action="store_false", help="evaluate only (default)"
    )
    mode.add_argument("--merge", action="store_true", help="squash-merge the pinned head on a pass")
    # Explicit, so a dry run is the default whatever order the flags are declared in.
    parser.set_defaults(merge=False)
    # The pinned script fixes the policy path; it cannot be overridden then.
    if policy_path is None:
        parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--source", default="unknown", help="where the gate was exported from")
    args = parser.parse_args(argv)

    policy = load_policy(policy_path or args.policy)
    try:
        facts = gather(policy, args.pr, runner)
    except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as exc:
        detail = (getattr(exc, "stderr", None) or "").strip()
        print(f"merge gate: PR #{args.pr}")
        print(f"  REFUSE  could not gather the facts: {exc} {detail}".rstrip())
        print("verdict: REFUSE (needs the owner)")
        return 1
    verdict = evaluate(
        policy, facts.pr, facts.changed_paths, facts.makefile, facts.plan, facts.plan_error
    )
    print(format_verdict(facts.pr, policy, verdict, args.source))
    if not verdict.allowed:
        return 1
    if args.merge:
        # --match-head-commit makes GitHub refuse if the head moved after evaluation.
        runner(merge_argv(facts.pr))
        print(f"merged PR #{facts.pr.number} at {facts.pr.head_sha}")
    else:
        print("dry run: nothing merged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
