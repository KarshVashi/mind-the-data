# ADR-005: CI roles are owned by the bootstrap, not the pipeline

Date: 2026-11-02
Status: proposed

## Context

GitHub Actions deploys `infra/envs/prod` through an OIDC role. That role's permissions have to be scoped to the resources this project creates. The question is which Terraform root manages the role's permissions.

If `envs/prod` managed them, the role applying `envs/prod` would need permission to edit its own policies. Any change merged to `main` could then grant the pipeline wider access, up to full control of the account, which also holds other projects.

A second constraint: the repository is private. On GitHub Free, a private repository's environment protection rules (required reviewers, deployment branches) are not enforced, so the `production` environment cannot be relied on as an approval gate.

## Decision

- **Bootstrap owns the CI access.** `infra/modules/ci_role` defines the CI roles and their permissions. It is applied only from `infra/bootstrap`, by a human, as `mtd-admin`.
- **Plan role (`mtd-ci-plan`), read-only.**
  - Assumable by `pull_request` jobs, and by jobs on `main` that plan before an apply.
  - Plans run with `-lock=false`.
- **Apply role (`mtd-ci-apply`), write access.**
  - Assumable only by jobs in the `production` environment.
  - It is created only when `enable_apply_role = true`. That waits until the environment's required reviewer and main-only branch rule are actually enforced, which needs a public repository or a paid plan.
  - Until then, `infra/envs/prod` is applied from a workstation by `mtd-admin` after review (runbook H7).
- **Approval sees the plan.** On `main`, the plan runs ungated first. The apply job, after approval, applies exactly that saved plan.
- **The pipeline can manage only `mind-the-data-*`.** That covers resources named `mind-the-data-*`, and Glue databases named `mind_the_data_*`.
  - S3 permissions are pinned to this account with `s3:ResourceAccount`.
  - Glue actions are listed explicitly. A wildcard would reach account-wide catalog settings.
- **Protected buckets.** The pipeline cannot:
  - delete a project bucket or object versions
  - change versioning
  - set ACLs
  - delete objects in the raw bucket (`mind-the-data-raw-*`)
- **Public access.** A bucket's public access block can't be protected from CI. Removing it is authorised as `s3:PutBucketPublicAccessBlock`, which creating one also needs. The account-level public access block (runbook H6 step 1) is the real control, and CI cannot change it.
- **No public functions.** The pipeline cannot create Lambda function URLs, or add resource policies to a function.
- **No account-wide listing.** PR jobs cannot list all Lambda functions, because that call returns every function's environment variables across the account.
- **Its own access is out of reach.** The CI roles and the boundary policy are named `mtd-*`, outside the prefix the pipeline can manage, so it cannot edit them.
- **Workload roles carry a boundary.** Every IAM role the pipeline creates must carry the `mtd-workload-boundary` permissions boundary. The pipeline cannot remove the boundary or publish a new version of it.
- **Three repository variables:** `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN` and `AWS_ROLE_ARN`, where `AWS_ROLE_ARN` is set only once the apply role exists. The original plan's single `AWS_ROLE_ARN` is not enough for two roles.

### Invariants later milestones must keep

- **Every `mind-the-data-*` IAM role carries the workload boundary.** `iam:UpdateAssumeRolePolicy` is not checked against the boundary, so a role created outside Terraform without the boundary could have its trust changed by CI. Create no such roles by hand.
- **Declare no `aws_s3_bucket_versioning` resource for the raw bucket**, not even `Disabled`. CI is denied `s3:PutBucketVersioning`.
- **Never read the TfL key through a Terraform `data "aws_ssm_parameter"`.** That would put the plaintext in state, which plan jobs (and so every pull request) can read. Lambdas read it at runtime instead, and Terraform refers to the parameter by ARN only.

## Alternatives considered

- **`ci_role` module applied from `envs/prod`.** This places it where the original task wording suggests, but it lets the pipeline escalate its own access.
- **A single role for both plan and apply.** Pull requests would then hold write credentials.
- **The AWS managed `ReadOnlyAccess` policy for plans.** It would let pull request jobs read every object and every Lambda configuration in the account, including other projects'.
- **Customising the OIDC subject claim to include the branch, so the apply role trusts `environment:production` and `main` together.** This would work, but it changes a repository-wide setting through the GitHub API and is easy to undo by accident. Enforced environment rules, or no apply role at all, are simpler.

## Accepted risks (review is the only control)

- **Lifecycle rules.** CI can still set lifecycle rules on project buckets, so an expiration rule on the raw bucket would delete raw data. The raw bucket's lifecycle needs particular care in review.
- **Bucket policies.** CI can still set bucket policies. The account-level block stops public policies, but not a grant to one named external account.

## Consequences

- **New permissions need a human.** When a milestone adds a new kind of resource, the permission change goes in `infra/modules/ci_role` and Karsh re-applies the bootstrap. Plans in CI fail with AccessDenied until then, which is noisy but safe.
  - M2's transform and publish roles will need Athena and Glue in the workload boundary.
  - Any bucket whose versioning CI must manage will need an exception to the bucket protections.
- **Deploying depends on the GitHub plan.** Until the repository is public or on a paid plan, deploying infrastructure is a reviewed local apply, so M1's deploy waits on a person rather than a click.
- **Plans can't lock state.** Plans run without a state lock. An apply rejects a saved plan if the state has moved since, so a stale plan can never be applied.
