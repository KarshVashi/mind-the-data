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
