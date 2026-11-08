"""Merge gate (ADR-011): policy file, evaluation rules and the gh/git edge.

Every test uses recorded `gh pr view` output or hand-built facts and a fake
runner; nothing reaches GitHub.
"""

import ast
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mind_the_data.ops import merge_gate as mg

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "merge_gate"
POLICY_PATH = ROOT / "config" / "merge_policy.yaml"
SHA = "ab735a56ab9049f9936ee4a069f85a2059cc229d"


@pytest.fixture(scope="module")
def policy() -> mg.Policy:
    return mg.load_policy(POLICY_PATH)


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def docs_pr() -> mg.PullRequest:
    return mg.parse_pr(fixture("pr_docs.json"))


def plan_changes() -> list[mg.PlanChange]:
    return mg.parse_plan_changes(json.loads((FIXTURES / "plan_changes.json").read_text()))


CI_OK = mg.Check("ci", "ci", "COMPLETED", "SUCCESS", "1")
PLAN_OK = mg.Check("infra", "plan", "COMPLETED", "SUCCESS", "2")
SKIPPED_DEPLOY = mg.Check("infra", "deploy", "COMPLETED", "SKIPPED", "2")


def code_pr(
    *files: str, checks: Sequence[mg.Check] = (CI_OK, PLAN_OK, SKIPPED_DEPLOY)
) -> mg.PullRequest:
    return replace(docs_pr(), files=tuple(files), checks=tuple(checks))


def refusals(verdict: mg.Verdict) -> str:
    return "\n".join(verdict.refused)


def test_policy_file_loads(policy: mg.Policy) -> None:
    assert policy.require.authors == ["KarshVashi"]
    assert policy.forbid.unlisted_types is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (("forbid", "unlisted_types"), False),
        (("require", "not_draft"), False),
        (("require", "checks_succeeded"), False),
        (("require", "authors"), []),
        (("require", "surprise"), True),
    ],
)
def test_policy_cannot_be_loosened_by_flags(
    tmp_path: Path, key: tuple[str, str], value: Any
) -> None:
    data = yaml.safe_load(POLICY_PATH.read_text())
    data[key[0]][key[1]] = value
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        mg.load_policy(path)


def test_policy_protects_what_the_owner_asked_for(policy: mg.Policy) -> None:
    must = [
        "infra/bootstrap/main.tf",
        ".github/workflows/ci.yml",
        "docs/adr/011-merge-gate.md",
        "config/merge_policy.yaml",
        "scripts/merge_gate.py",
        "src/mind_the_data/ops/merge_gate.py",
        "infra/modules/budget/main.tf",
        "docs/Quotas.md",
    ]
    assert mg.matching(must, policy.protected_paths) == sorted(must)


def test_plan_required_paths_match_the_infra_workflow(policy: mg.Policy) -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "infra.yml").read_text())
    # PyYAML reads the `on` key as True.
    triggers = workflow[True]["pull_request"]["paths"]
    assert sorted(policy.plan_required_paths) == sorted(triggers)


def test_allowed_types_exclude_iam_and_exist_in_infra(policy: mg.Policy) -> None:
    used = set()
    for path in (ROOT / "infra").rglob("*.tf"):
        used |= set(re.findall(r'^resource "([a-z0-9_]+)"', path.read_text(), re.MULTILINE))
    for t in policy.allowed_resource_types:
        assert not t.startswith("aws_iam_")
        assert t in used, f"{t} is allowed but no Terraform uses it"
    assert "aws_athena_workgroup" not in policy.allowed_resource_types


@pytest.mark.parametrize(
    ("pattern", "path", "hit"),
    [
        ("infra/bootstrap/**", "infra/bootstrap/main.tf", True),
        ("infra/bootstrap/**", "infra/bootstrap/x/y.tf", True),
        ("infra/bootstrap/**", "infra/bootstrapper/main.tf", False),
        ("**/*budget*", "budget.md", True),
        ("**/*budget*", "infra/modules/cost/Budget.tf", True),
        ("src/mind_the_data/ops/merge_gate*", "src/mind_the_data/ops/merge_gate.py", True),
        ("src/mind_the_data/ops/merge_gate*", "src/mind_the_data/ops/x/merge_gate.py", False),
        ("scripts/*.py", "scripts/a/b.py", False),
        (".github/**", ".GitHub/workflows/x.yml", True),
    ],
)
def test_glob(pattern: str, path: str, hit: bool) -> None:
    assert bool(mg.glob_regex(pattern).fullmatch(path)) is hit


def test_parse_pr_reads_checks_and_run_ids() -> None:
    pr = mg.parse_pr(fixture("pr_workflows.json"))
    plan = [c for c in pr.checks if c.workflow == "infra" and c.name == "plan"]
    assert plan and plan[0].run_id and plan[0].run_id.isdigit()
    assert ".github/workflows/infra.yml" in pr.files


def test_parse_pr_reads_status_contexts() -> None:
    data = fixture("pr_docs.json")
    data["statusCheckRollup"].append(
        {"__typename": "StatusContext", "context": "external", "state": "PENDING"}
    )
    pr = mg.parse_pr(data)
    assert pr.checks[-1] == mg.Check("", "external", "PENDING", "")


def test_parse_plan_changes_rejects_bad_shapes() -> None:
    with pytest.raises(TypeError):
        mg.parse_plan_changes({"resource_changes": []})
    with pytest.raises(ValueError):
        mg.parse_plan_changes([{"address": "a", "mode": "managed", "actions": ["create"]}])
    with pytest.raises(TypeError):
        mg.parse_plan_changes(
            [{"address": "a", "mode": "managed", "type": "t", "actions": ["create"]}]
        )
    with pytest.raises(ValueError):
        mg.parse_plan_changes(
            [{"address": "a", "mode": "managed", "type": "t", "actions": "create"}]
        )


def test_docs_pr_is_allowed(policy: mg.Policy) -> None:
    pr = docs_pr()
    verdict = mg.evaluate(policy, pr, pr.files, None, None)
    assert verdict.allowed, refusals(verdict)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"is_draft": True}, "draft"),
        ({"author": "someone-else"}, "allowlist"),
        ({"state": "MERGED"}, "MERGED"),
        ({"base_ref": "release"}, "base branch"),
        ({"title": "fix: drop table [allow-destroy]"}, "[allow-destroy]"),
        ({"commit_messages": ("chore: tidy\n[ALLOW-DESTROY]",)}, "[allow-destroy]"),
        ({"checks": ()}, "no checks"),
        ({"checks": (mg.Check("ci", "ci", "IN_PROGRESS", ""),)}, "ci / ci has not succeeded"),
        ({"checks": (mg.Check("ci", "ci", "COMPLETED", "FAILURE"),)}, "ci / ci has not succeeded"),
        (
            {"checks": (mg.Check("other", "lint", "COMPLETED", "SUCCESS"),)},
            "ci / ci has not succeeded",
        ),
        ({"checks": (CI_OK, mg.Check("x", "y", "COMPLETED", "CANCELLED"))}, "x / y"),
        ({"checks": (CI_OK, mg.Check("", "ext", "PENDING", ""))}, "ext"),
        ({"checks": (CI_OK, mg.Check("", "ext", "COMPLETED", "ERROR"))}, "ext"),
    ],
)
def test_pr_facts_that_refuse(policy: mg.Policy, change: dict[str, Any], reason: str) -> None:
    pr = replace(docs_pr(), **change)
    verdict = mg.evaluate(policy, pr, pr.files, None, None)
    assert not verdict.allowed
    assert reason.lower() in refusals(verdict).lower()


def test_skipped_optional_checks_are_fine(policy: mg.Policy) -> None:
    pr = replace(
        docs_pr(), checks=(CI_OK, mg.Check("infra", "not-configured", "COMPLETED", "SKIPPED"))
    )
    assert mg.evaluate(policy, pr, pr.files, None, None).allowed


def test_workflow_pr_is_refused(policy: mg.Policy) -> None:
    pr = mg.parse_pr(fixture("pr_workflows.json"))
    verdict = mg.evaluate(policy, pr, pr.files, None, None, "not found")
    assert not verdict.allowed
    assert ".github/workflows/infra.yml" in refusals(verdict)


def test_no_changed_files_is_refused(policy: mg.Policy) -> None:
    verdict = mg.evaluate(policy, docs_pr(), (), None, None)
    assert "no changed files" in refusals(verdict)


def test_code_change_with_an_allowed_plan_passes(policy: mg.Policy) -> None:
    pr = code_pr("src/mind_the_data/ingest/client.py")
    verdict = mg.evaluate(policy, pr, pr.files, None, plan_changes())
    assert verdict.allowed, refusals(verdict)
    # Two real changes: the no-op and data reads are not counted.
    assert any("2 resource change(s)" in p for p in verdict.passed)


def test_code_change_without_plan_evidence_is_refused(policy: mg.Policy) -> None:
    pr = code_pr("src/mind_the_data/ingest/client.py")
    verdict = mg.evaluate(policy, pr, pr.files, None, None, "artifact not found")
    assert "no plan-changes evidence" in refusals(verdict)
    assert "artifact not found" in refusals(verdict)


@pytest.mark.parametrize("conclusion", ["SKIPPED", "FAILURE"])
def test_code_change_needs_the_plan_job_to_succeed(policy: mg.Policy, conclusion: str) -> None:
    pr = code_pr(
        "infra/envs/prod/main.tf",
        checks=(CI_OK, mg.Check("infra", "plan", "COMPLETED", conclusion)),
    )
    verdict = mg.evaluate(policy, pr, pr.files, None, plan_changes())
    assert "infra / plan has not succeeded" in refusals(verdict)


def change(
    type_: str, actions: Sequence[str], mode: str = "managed", importing: bool = False
) -> mg.PlanChange:
    return mg.PlanChange(f"module.x.{type_}.this", mode, type_, tuple(actions), importing)


@pytest.mark.parametrize(
    ("planned", "reason"),
    [
        (change("aws_iam_role_policy", ["update"]), "aws_iam_role_policy is forbidden"),
        (change("aws_iam_role", ["create"]), "aws_iam_role is forbidden"),
        (change("aws_lambda_function", ["delete"]), "delete is forbidden"),
        (change("aws_lambda_function", ["delete", "create"]), "delete is forbidden"),
        (change("aws_lambda_function", ["create", "delete"]), "delete is forbidden"),
        (change("aws_athena_workgroup", ["update"]), "not an allowed type"),
        (change("aws_budgets_budget", ["update"]), "not an allowed type"),
        (change("aws_lambda_function", ["forget"]), "unexpected actions"),
        (change("aws_lambda_function", ["create"], mode="data"), "unexpected mode"),
        (change("aws_iam_role", ["no-op"], importing=True), "imports are not allowed"),
        (change("aws_lambda_function", ["update"], importing=True), "imports are not allowed"),
        (change("aws_s3_bucket_lifecycle_configuration", ["update"]), "not an allowed type"),
    ],
)
def test_plan_rules_refuse(policy: mg.Policy, planned: mg.PlanChange, reason: str) -> None:
    pr = code_pr("infra/envs/prod/main.tf")
    verdict = mg.evaluate(policy, pr, pr.files, None, [*plan_changes(), planned])
    assert not verdict.allowed
    assert reason in refusals(verdict)


START = "# >>> merge gate (protected: config/merge_policy.yaml)"
END = "# <<< merge gate"
BLOCK = f"{START}\nmerge:\n\tgate\n{END}\n"
BASE = "check:\n\tpytest\n\n" + BLOCK


def mk(head: str, changed: Sequence[str], base: str = BASE) -> mg.MakefileDiff:
    return mg.MakefileDiff(base=base, head=head, changed_lines=tuple(changed))


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        (mk(BASE.replace("\tgate", "\tgh pr merge"), ["\tgh pr merge", "\tgate"]), "block"),
        (mk(BASE.replace(END, ""), [END]), "malformed"),
        (mk("check:\n\tpytest\n", [START, "merge:"]), "block"),
        (mk(BASE + "merge:\n\tgh pr merge 1\n", ["merge:", "\tgh pr merge 1"]), "changed line"),
        (mk(BASE + "SHELL := /bin/sh\n", ["SHELL := /bin/sh"]), "changed line"),
        (
            mk(BASE + "export PATH := /tmp:$(PATH)\n", ["export PATH := /tmp:$(PATH)"]),
            "changed line",
        ),
        (mk(BASE + "include extra.mk\n", ["include extra.mk"]), "changed line"),
        (mk(BASE + "%:\n\techo\n", ["%:", "\techo"]), "changed line"),
        (mk(BASE + "m%: export GIT_DIR=/x\n", ["m%: export GIT_DIR=/x"]), "changed line"),
        (
            mk(BASE + "m%: UV_PROJECT_ENVIRONMENT=/x\n", ["m%: UV_PROJECT_ENVIRONMENT=/x"]),
            "changed line",
        ),
        (mk(BASE + "X := $(shell id)\n", ["X := $(shell id)"]), "changed line"),
        (mk(BASE + "$(eval X := 1)\n", ["$(eval X := 1)"]), "changed line"),
        (mk(BASE + ".RECIPEPREFIX = >\n", [".RECIPEPREFIX = >"]), "changed line"),
        (mk("ifeq (1,0)\n" + BASE, ["ifeq (1,0)"]), "changed line"),
        (mk("define x\n" + BASE, ["define x"]), "changed line"),
    ],
)
def test_makefile_changes_that_touch_the_gate(diff: mg.MakefileDiff, reason: str) -> None:
    why = mg.makefile_touches_gate(diff, mg.load_policy(POLICY_PATH).makefile)
    assert why is not None and reason in why


def test_makefile_change_elsewhere_is_fine(policy: mg.Policy) -> None:
    head = BASE.replace("\tpytest", "\tpytest -q")
    diff = mk(head, ["\tpytest", "\tpytest -q"])
    assert mg.makefile_touches_gate(diff, policy.makefile) is None
    pr = code_pr("Makefile")
    assert mg.evaluate(policy, pr, pr.files, diff, plan_changes()).allowed


def test_branch_from_before_the_gate_is_fine(policy: mg.Policy) -> None:
    diff = mk("check:\n\tpytest -q\n", ["\tpytest -q"], base="check:\n\tpytest\n")
    assert mg.makefile_touches_gate(diff, policy.makefile) is None


def test_unreadable_makefile_diff_is_refused(policy: mg.Policy) -> None:
    pr = code_pr("Makefile")
    verdict = mg.evaluate(policy, pr, pr.files, None, plan_changes())
    assert "diff could not be read" in refusals(verdict)


def test_changed_lines_skips_file_headers() -> None:
    diff = "--- a/Makefile\n+++ b/Makefile\n@@ -1 +1 @@\n-old\n+new\n"
    assert mg.changed_lines(diff) == ("old", "new")


def test_repository_makefile_has_one_guarded_block(policy: mg.Policy) -> None:
    text = (ROOT / "Makefile").read_text()
    block = mg._block(text, policy.makefile)
    assert block
    for needle in ("merge-check:", "merge:", "git archive", "python -I", "--merge", "--dry-run"):
        assert needle in block
    # The real merge never takes the overridable ref.
    merge_recipe = block.split("\nmerge:", 1)[1]
    assert "MERGE_GATE_REF" not in merge_recipe and "origin/main" in merge_recipe


class FakeRunner:
    def __init__(
        self,
        pr: dict[str, Any],
        plan: Any = None,
        run_sha: str = SHA,
        log: str = "",
        event: str = "pull_request",
    ) -> None:
        self.pr, self.plan, self.run_sha, self.log, self.event = pr, plan, run_sha, log, event
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        argv = list(argv)
        self.calls.append(argv)
        match argv[:3]:
            case ["gh", "pr", "view"]:
                return json.dumps(self.pr)
            case ["git", "fetch", _]:
                return ""
            case ["git", "merge-base", _]:
                return "base0\n"
            case ["git", "diff", "--name-only"]:
                return "\0".join(f["path"] for f in self.pr["files"]) + "\0"
            case ["git", "log", _]:
                return self.log
            case ["git", "show", _]:
                return BASE
            case ["git", "diff", "--no-renames"]:
                return ""
            case ["gh", "run", "view"]:
                return json.dumps(
                    {"headSha": self.run_sha, "event": self.event, "workflowName": "infra"}
                )
            case ["gh", "run", "download"]:
                if self.plan is None:
                    raise subprocess.CalledProcessError(1, argv, stderr="no artifact matches\n")
                out = Path(argv[argv.index("-D") + 1])
                (out / "plan-changes.json").write_text(json.dumps(self.plan))
                return ""
            case ["gh", "pr", "merge"]:
                return ""
        raise AssertionError(f"unexpected command {argv}")


def merged(runner: FakeRunner) -> list[list[str]]:
    return [c for c in runner.calls if c[:3] == ["gh", "pr", "merge"]]


def test_dry_run_is_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(fixture("pr_docs.json"))
    assert mg.main(["19"], runner, POLICY_PATH) == 0
    assert not merged(runner)
    out = capsys.readouterr().out
    assert "verdict: ALLOW" in out and "dry run: nothing merged" in out


def test_merge_pins_the_head_commit() -> None:
    runner = FakeRunner(fixture("pr_docs.json"))
    assert mg.main(["19", "--merge"], runner, POLICY_PATH) == 0
    assert merged(runner) == [
        ["gh", "pr", "merge", "19", "--squash", "--match-head-commit", SHA, "--delete-branch=false"]
    ]


def test_refusal_exits_non_zero_and_never_merges(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(fixture("pr_workflows.json"))
    assert mg.main(["20", "--merge"], runner, POLICY_PATH) == 1
    assert not merged(runner)
    assert "verdict: REFUSE" in capsys.readouterr().out


def src_pr() -> dict[str, Any]:
    data = fixture("pr_docs.json")
    data["files"] = [{"path": "src/mind_the_data/ingest/client.py"}]
    data["statusCheckRollup"].append(
        {
            "__typename": "CheckRun",
            "workflowName": "infra",
            "name": "plan",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://github.com/o/r/actions/runs/777/job/1",
        }
    )
    return data


def test_plan_artifact_is_downloaded_for_the_plan_run(policy: mg.Policy) -> None:
    runner = FakeRunner(src_pr(), plan=json.loads((FIXTURES / "plan_changes.json").read_text()))
    facts = mg.gather(policy, 19, runner)
    assert facts.plan == plan_changes()
    download = next(c for c in runner.calls if c[:3] == ["gh", "run", "download"])
    assert download[3:6] == ["777", "-n", "plan-changes"]
    assert mg.main(["19", "--merge"], runner, POLICY_PATH) == 0


def test_missing_plan_artifact_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(src_pr(), plan=None)
    assert mg.main(["19", "--merge"], runner, POLICY_PATH) == 1
    assert not merged(runner)
    assert "no artifact matches" in capsys.readouterr().out


def test_plan_from_another_commit_refuses(policy: mg.Policy) -> None:
    runner = FakeRunner(src_pr(), plan=[], run_sha="0" * 40)
    facts = mg.gather(policy, 19, runner)
    assert facts.plan is None and facts.plan_error and "not the head" in facts.plan_error


def test_renamed_away_paths_from_git_are_included(policy: mg.Policy) -> None:
    runner = FakeRunner(fixture("pr_docs.json"))
    original = runner.__call__

    def with_rename(argv: Sequence[str]) -> str:
        if list(argv[:3]) == ["git", "diff", "--name-only"]:
            return "README.md\0scripts/merge_gate.py\0"
        return original(argv)

    facts = mg.gather(policy, 19, with_rename)
    assert "scripts/merge_gate.py" in facts.changed_paths


def test_gate_module_imports_nothing_from_the_package() -> None:
    tree = ast.parse((ROOT / "src" / "mind_the_data" / "ops" / "merge_gate.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module and not node.module.startswith("mind_the_data")
            assert node.level == 0
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("mind_the_data") for a in node.names)


def test_plan_artifact_step_carries_no_values() -> None:
    text = (ROOT / ".github" / "workflows" / "infra.yml").read_text()
    jobs = yaml.safe_load(text)["jobs"]
    steps = jobs["plan"]["steps"]
    jq = next(s["run"] for s in steps if s.get("name") == "plan changes for the merge gate")
    assert "before" not in jq and "after" not in jq and "address" in jq
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == "plan-changes"
    assert upload["with"]["retention-days"] <= 7


def test_script_loads_the_module_by_path_under_isolated_mode() -> None:
    # -I drops the script's directory and the working tree from sys.path, as
    # `make merge` runs it; --help exits before any gh or git call.
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "merge_gate.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--merge" in result.stdout and "--dry-run" in result.stdout


def test_gnumakefile_and_lowercase_makefile_are_protected(policy: mg.Policy) -> None:
    for path in ("GNUmakefile", "makefile"):
        pr = replace(docs_pr(), files=(path,))
        verdict = mg.evaluate(policy, pr, pr.files, None, None)
        assert f"protected paths changed: {path}" in refusals(verdict)
    # `Makefile` itself goes through the block check, not the protected list.
    assert "Makefile" not in mg.matching(["Makefile"], policy.protected_paths)


def test_a_failed_rerun_beside_a_success_refuses(policy: mg.Policy) -> None:
    failed = mg.Check("ci", "ci", "COMPLETED", "FAILURE")
    pr = replace(docs_pr(), checks=(CI_OK, failed))
    assert "ci / ci has not succeeded" in refusals(mg.evaluate(policy, pr, pr.files, None, None))


def test_dry_run_and_merge_are_exclusive() -> None:
    with pytest.raises(SystemExit):
        mg.main(["19", "--dry-run", "--merge"], FakeRunner(fixture("pr_docs.json")), POLICY_PATH)


def test_pinned_script_cannot_take_another_policy() -> None:
    with pytest.raises(SystemExit):
        mg.main(["19", "--policy", "/tmp/p.yaml"], FakeRunner(fixture("pr_docs.json")), POLICY_PATH)


def test_marker_in_a_commit_beyond_ghs_list_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(fixture("pr_docs.json"), log="chore: x\n[allow-destroy]\n\0")
    assert mg.main(["19", "--merge"], runner, POLICY_PATH) == 1
    assert not merged(runner)


def test_manual_plan_run_is_not_evidence(policy: mg.Policy) -> None:
    runner = FakeRunner(src_pr(), plan=[], event="workflow_dispatch")
    facts = mg.gather(policy, 19, runner)
    assert facts.plan is None and facts.plan_error and "pull_request" in facts.plan_error


def test_gather_uses_the_full_main_ref() -> None:
    runner = FakeRunner(fixture("pr_docs.json"))
    mg.gather(mg.load_policy(POLICY_PATH), 19, runner)
    assert ["git", "merge-base", "refs/remotes/origin/main", SHA] in runner.calls
    fetches = [c for c in runner.calls if c[:2] == ["git", "fetch"]]
    assert all("--no-tags" in c for c in fetches)


def test_gather_failure_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(argv: Sequence[str]) -> str:
        raise subprocess.CalledProcessError(1, list(argv), stderr="gh: not logged in")

    assert mg.main(["19", "--merge"], broken, POLICY_PATH) == 1
    out = capsys.readouterr().out
    assert "REFUSE" in out and "not logged in" in out


def test_makefile_recipe_pins_main() -> None:
    block = mg._block((ROOT / "Makefile").read_text(), mg.load_policy(POLICY_PATH).makefile)
    assert block
    assert "MERGE_GATE_MAIN := refs/remotes/origin/main" in block
    assert "git fetch --quiet --no-tags origin +refs/heads/main:$(MERGE_GATE_MAIN)" in block
    assert "$(call merge_gate,$(MERGE_GATE_MAIN),--merge)" in block
    assert "$(call merge_gate,$(MERGE_GATE_REF),--dry-run)" in block
    assert 'git archive "$(1)"' in block and "python -I" in block
    assert 'UV_PROJECT_ENVIRONMENT="$$tmp/.venv"' in block
    assert "=~ ^[0-9]+$$" in block


def test_reading_a_uv_variable_is_fine(policy: mg.Policy) -> None:
    line = "\t@$(UV_RUN) python scripts/status.py"
    diff = mk(BASE + "status:\n" + line + "\n", ["status:", line])
    assert mg.makefile_touches_gate(diff, policy.makefile) is None
