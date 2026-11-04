# ADR-005: CI roles and deploy approval

Date: 2026-11-03
Status: accepted

## Context

GitHub Actions deploys `infra/envs/prod` through an OIDC role. Two questions:

1. **Who manages that role's permissions?** If `envs/prod` managed them, the role applying `envs/prod` would need permission to edit its own policies. Any change merged to `main` could then grant the pipeline wider access, up to full control of the account, which also holds other projects.
2. **What approves a deploy?** The original plan used a GitHub `production` environment with a required reviewer. On GitHub Free, Pro and Team, required reviewers only work on public repositories, and this repository is private. Without that rule, an environment-based role would be open to any branch that names the environment.

## Decision

### Merging is the approval

- **Pull requests only plan.** A pull request that touches `infra/` gets a read-only `terraform plan`, which is reviewed on the pull request.
- **Merging deploys.** When the pull request is merged into `main`, the run on `main` plans again and applies that plan.
- **No GitHub environment or manual approval step is used.**
- **AWS enforces the branch rule, not the workflow file.** Each role's trust policy checks the subject of the job's OIDC token:
  - The plan role (`mtd-ci-plan`) accepts only `repo:KarshVashi/mind-the-data:pull_request`.
  - The apply role (`mtd-ci-apply`) accepts only `repo:KarshVashi/mind-the-data:ref:refs/heads/main`.

  A pull request job therefore cannot get write credentials, even if it rewrites the workflow. A job that names a GitHub environment gets an environment subject, and is refused.

### The bootstrap owns CI access

- **Human-applied.** `infra/modules/ci_role` defines both roles, their permissions and the workload boundary. It is applied only from `infra/bootstrap`, by a human, as `mtd-admin`.
- **Plans don't lock state.** Plans run with `-lock=false`, because the plan role is read-only.
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
- **No account-wide listing.** Pull request jobs cannot list all Lambda functions, because that call returns every function's environment variables across the account.
- **Its own access is out of reach.** The CI roles and the boundary policy are named `mtd-*`, outside the prefix the pipeline can manage, so it cannot edit them.
- **Workload roles carry a boundary.** Every IAM role the pipeline creates must carry the `mtd-workload-boundary` permissions boundary. The pipeline cannot remove the boundary or publish a new version of it.
- **Three repository variables:** `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN` and `AWS_ROLE_ARN`. The original plan's single `AWS_ROLE_ARN` is not enough for two roles.

### Invariants later milestones must keep

- **Every `mind-the-data-*` IAM role carries the workload boundary.** `iam:UpdateAssumeRolePolicy` is not checked against the boundary, so a role created outside Terraform without the boundary could have its trust changed by CI. Create no such roles by hand.
- **Declare no `aws_s3_bucket_versioning` resource for the raw bucket**, not even `Disabled`. CI is denied `s3:PutBucketVersioning`.
- **Never read the TfL key through a Terraform `data "aws_ssm_parameter"`.** That would put the plaintext in state, which plan jobs (and so every pull request) can read. Lambdas read it at runtime instead, and Terraform refers to the parameter by ARN only.
- **Scheduled and publishing workflows on `main` get their own roles (M2, M5).** They must not reuse the apply role, which any job on `main` can assume.

## Alternatives considered

- **A `production` environment with a required reviewer.** This is the original plan. It needs a public repository, or GitHub Enterprise for a private one.
- **Deploying from a workstation after merge.** This works on any plan, but every infra change needs a manual step. It is kept in the runbook as a fallback for when CI cannot deploy.
- **`ci_role` module applied from `envs/prod`.** This lets the pipeline escalate its own access.
- **A single role for both plan and apply.** Pull requests would then hold write credentials.
- **The AWS managed `ReadOnlyAccess` policy for plans.** It would let pull request jobs read every object and every Lambda configuration in the account, including other projects'.

## Accepted risks

- **No second look before apply.** A merge to `main` applies without a further approval step. The reviewed plan is the one on the pull request.
  - Deletes and replacements, the changes an approval step mostly existed to catch, are blocked unless opted in: `[allow-destroy]` in the pull request title, or the `allow_destroy` input on a manual run.
  - Pull request plans flag them with a warning.
- **The deploy plan can differ from the reviewed one.** If `main` moved after the pull request's plan ran, the deploy includes those changes too. Requiring branches to be up to date would prevent this, but on a private repository that needs a paid plan. The deploy job writes the plan it applied to the run summary.
- **A direct push to `main` deploys.** On a free private repository, branch protection is not enforced, so nothing forces changes through a pull request. Only Karsh has write access, and the local tooling blocks pushes to `main`. Revisit this ADR before giving anyone else write access.
- **Any job running on `main` can assume the apply role.** The OIDC subject is the same for every event that runs on the default branch: `push`, `workflow_dispatch`, `schedule`, `workflow_run`, `issue_comment`, `repository_dispatch`. A later workflow with `id-token: write` on any of those triggers could get deploy credentials without a new merge of infra code.
  - Workflows themselves still reach `main` only through Karsh's merge, and later workflows get their own roles (see the invariants).
  - A stronger control is available if this ever matters: customise the repository's OIDC subject to include `job_workflow_ref`, and trust only `infra.yml` on `main`.
- **Lifecycle rules.** CI can still set lifecycle rules on project buckets, so an expiration rule on the raw bucket would delete raw data. The raw bucket's lifecycle needs particular care in review.
- **Bucket policies.** CI can still set bucket policies. The account-level block stops public policies, but not a grant to one named external account.
- **Plans in logs.** Plan text is written to job logs and run summaries, which keep it for the repository's log retention (90 days by default). Plans can contain values such as the alarm email address. Lower the retention in the repository's Actions settings if that matters.

## Consequences

- **Deploys are automatic.** Infra changes deploy without a manual step, on a free private repository.
- **New permissions need a human.** When a milestone adds a new kind of resource, the permission change goes in `infra/modules/ci_role` and Karsh re-applies the bootstrap. Plans in CI fail with AccessDenied until then, which is noisy but safe.
  - M2's transform and publish roles will need Athena and Glue in the workload boundary. (Superseded, see the M2 amendment below.)
  - Any bucket whose versioning CI must manage will need an exception to the bucket protections.
- **Revisit triggers:**
  - The repository becomes public. The environment approval gate then becomes available at no cost.
  - A second person gets write access.

## Amendment (M2, 2026-11-03): transform role

- **The transform role is a CI role, not a workload role.** `mtd-ci-transform` is defined in `infra/modules/ci_role` next to the plan and apply roles, applied from the bootstrap by a human, and named `mtd-*` so the pipeline cannot edit it. It carries its own inline policy (prod workgroup, read on raw, write on `mind_the_data_prod` and the `prod/` prefixes of the curated and results buckets) rather than a permissions boundary. The workload boundary is unchanged: it only limits roles the pipeline creates, and the pipeline does not create this one.
- **Four repository variables for AWS access:** `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN`, `AWS_ROLE_ARN` and `AWS_TRANSFORM_ROLE_ARN`.
- **Same subject, separate role.** The transform role trusts the same subject as the apply role, the immutable form from ADR-009 (`repo:KarshVashi@41749592/mind-the-data@1442302649:ref:refs/heads/main`), so the accepted risk "any job running on `main` can assume" applies to it too, with far less reach than the apply role.
