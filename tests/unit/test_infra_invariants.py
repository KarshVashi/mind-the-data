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
