"""ADR-005 invariants for the Terraform the pipeline deploys.

A breach would otherwise only show up as an AccessDenied at deploy time, or
not at all (a role without the boundary can be widened by CI). These are
text checks, so they are deliberately strict about layout: one resource per
block, `permissions_boundary` set inside every aws_iam_role block.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
# The bootstrap and ci_role are applied by a human and own the CI roles.
DEPLOYED_ROOTS = [INFRA / "envs", INFRA / "modules"]
EXCLUDED = INFRA / "modules" / "ci_role"


def deployed_tf_files() -> list[Path]:
    return sorted(
        path
        for root in DEPLOYED_ROOTS
        for path in root.rglob("*.tf")
        if EXCLUDED not in path.parents and ".terraform" not in path.parts
    )


def blocks(text: str, block_type: str, label: str) -> list[str]:
    """Bodies of every `<block_type> "<label>" "<name>" { ... }` block."""
    bodies = []
    for match in re.finditer(rf'^{block_type} "{label}" "[^"]+" \{{', text, re.MULTILINE):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        bodies.append(text[match.end() : i - 1])
    return bodies


def test_deployed_terraform_exists() -> None:
    assert deployed_tf_files(), "no Terraform found; the paths in this test are stale"


def test_every_iam_role_carries_the_workload_boundary() -> None:
    for path in deployed_tf_files():
        for body in blocks(path.read_text(), "resource", "aws_iam_role"):
            assert re.search(r"^\s*permissions_boundary\s*=", body, re.MULTILINE), (
                f"{path}: aws_iam_role without permissions_boundary"
            )


def test_no_bucket_versioning_resource() -> None:
    for path in deployed_tf_files():
        assert 'resource "aws_s3_bucket_versioning"' not in path.read_text(), (
            f"{path}: CI is denied s3:PutBucketVersioning"
        )


def test_app_key_never_read_by_terraform() -> None:
    for path in deployed_tf_files():
        assert 'data "aws_ssm_parameter"' not in path.read_text(), (
            f"{path}: reading the key through a data source puts it in state"
        )


def test_no_function_urls_or_lambda_permissions() -> None:
    for path in deployed_tf_files():
        text = path.read_text()
        for denied in ("aws_lambda_function_url", "aws_lambda_permission"):
            assert f'resource "{denied}"' not in text, f"{path}: CI is denied {denied}"


def test_raw_bucket_has_no_expiration() -> None:
    text = (INFRA / "modules" / "raw_bucket" / "main.tf").read_text()
    assert not re.search(r"^\s*(noncurrent_version_)?expiration\s*\{", text, re.MULTILINE)


def test_raw_tables_never_hide_bad_lines() -> None:
    # Either property turns a malformed or mistyped envelope into nulls, which
    # downstream would read as a quiet period rather than missing data.
    for path in (INFRA / "modules" / "catalog").glob("*.tf"):
        text = path.read_text()
        for prop in ("ignore.malformed.json", "use.null.for.invalid.data"):
            assert f'"{prop}"' not in text, f"{path}: {prop} would hide bad raw lines"


def test_transform_role_names_match_the_prod_stack() -> None:
    # The bootstrap cannot read envs/prod outputs, so ci_role rebuilds the
    # names from the same patterns; a rename on one side only would surface
    # as AccessDenied in the first scheduled build.
    ci_role = (INFRA / "modules" / "ci_role" / "main.tf").read_text()
    prod = (INFRA / "envs" / "prod" / "main.tf").read_text()
    for pattern in (
        "-athena-results-${local.account_id}",
        "-curated-${local.account_id}",
        "-raw-${local.account_id}",
    ):
        assert pattern in ci_role and pattern in prod, pattern
    assert '"${local.glue_prefix}_raw"' in prod and "${local.glue_p}_raw" in ci_role
    assert (
        '"${local.prefix}-${target}"' in prod
        and "workgroup/${local.p}-${var.transform_target}" in ci_role
    )


def test_transform_role_cannot_write_raw() -> None:
    text = (INFRA / "modules" / "ci_role" / "main.tf").read_text()
    policies = blocks(text, "data", "aws_iam_policy_document")
    transform = next(b for b in policies if "GlueTargetWrite" in b)
    for statement in transform.split("statement {")[1:]:
        if re.search(r'"(s3:(Put|Delete)|glue:(Create|Update|Delete|Batch))', statement):
            assert "raw" not in statement, statement


CI_ROLE = INFRA / "modules" / "ci_role" / "main.tf"
CI_ROLES = ("plan", "apply", "transform", "observer", "publish")


def trust_entries(text: str) -> dict[str, str]:
    """Body of each role's list in `trust = { <role> = [ ... ] }`."""
    start = text.index("  trust = {")
    bodies = {}
    for role in CI_ROLES:
        match = re.compile(rf"^    {role} = \[", re.MULTILINE).search(text, start)
        assert match, f"no trust entry for {role}"
        depth, i = 1, match.end()
        while depth:
            depth += {"[": 1, "]": -1}.get(text[i], 0)
            i += 1
        bodies[role] = text[match.end() : i - 1]
    return bodies


def test_ci_role_trust_uses_the_immutable_subject() -> None:
    # ADR-009: a subject built from the repository name would follow a rename
    # or transfer; every trust policy must use the id-qualified prefix.
    text = CI_ROLE.read_text()
    main_subs = re.findall(
        r"^\s+\w+\s+=\s+\"(\$\{[^\"]+)\"$",
        text.split("main_sub = {")[1].split("}\n")[0],
        re.MULTILINE,
    )
    assert len(main_subs) == 3, main_subs
    for subject in main_subs:
        assert subject.startswith("${var.github_subject_prefix}:ref:refs/heads/"), subject
    for role, body in trust_entries(text).items():
        subjects = re.findall(r"^\s+sub\s+=\s+(.+)$", body, re.MULTILINE)
        assert subjects, role
        for subject in subjects:
            assert subject.startswith(('"${var.github_subject_prefix}:', "local.main_sub.")), (
                role,
                subject,
            )
    # The single trust document carries every entry's sub condition.
    assert 'variable = "${local.oidc_host}:sub"' in text
    assert "var.github_repository" not in text


def test_new_trust_is_always_bound_to_workflows() -> None:
    # ADR-010: roles added after the binding exist only for named workflows.
    # Only the roles in use before ADR-010 may wait for enforce_workflow_binding.
    entries = trust_entries(CI_ROLE.read_text())
    for role in ("observer", "publish"):
        assert "enforce_workflow_binding" not in entries[role], role
        assert "local.workflow_files." in entries[role], role
    plan_main = entries["plan"].split("},")[1]
    assert "enforce_workflow_binding" not in plan_main
    assert "workflow_files.plan_main" in plan_main
    for role in ("apply", "transform"):
        assert f"local.workflow_files.{role}" in entries[role], role


def test_bound_workflow_files_exist_or_are_contracted() -> None:
    # The names are a contract with .github/workflows. Files that other work
    # adds later (health checks, weekly publish) are listed here explicitly.
    text = CI_ROLE.read_text()
    files = set(
        re.findall(r'"([a-z0-9-]+\.yml)"', text.split("workflow_files = {")[1].split("}")[0])
    )
    pending = {"ops-daily.yml", "ops-daytime.yml", "publish-weekly.yml"}
    workflows = INFRA.parent / ".github" / "workflows"
    assert pending <= files, files
    for name in files - pending:
        assert (workflows / name).is_file(), name


def test_job_workflow_ref_is_matched() -> None:
    text = CI_ROLE.read_text()
    assert 'variable = "${local.oidc_host}:job_workflow_ref"' in text
    # Only pull request refs vary (refs/pull/<n>/merge); everything else is exact.
    assert text.count('test      = "StringLike"') == 1
    assert "@refs/pull/*/merge" in text


def test_bootstrap_binds_the_pre_adr_010_roles() -> None:
    # ADR-010 step 3: the probe passed, so the plan (pull request), apply and
    # transform roles are bound too. Turning this off is a recorded fallback,
    # not a quiet default.
    variables = (INFRA / "bootstrap" / "variables.tf").read_text()
    block = variables.split('variable "enforce_workflow_binding" {')[1].split("\n}\n")[0]
    assert re.search(r"^\s+default\s+=\s+true$", block, re.MULTILINE), block
    main = (INFRA / "bootstrap" / "main.tf").read_text()
    assert re.search(
        r"^\s+enforce_workflow_binding\s+=\s+var\.enforce_workflow_binding$", main, re.MULTILINE
    )
