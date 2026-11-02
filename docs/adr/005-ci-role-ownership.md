# ADR-005: CI roles are owned by the bootstrap, not the pipeline

Date: 2026-11-02
Status: proposed

## Context

GitHub Actions deploys `infra/envs/prod` through an OIDC role. That role's permissions have to be scoped to the resources this project creates. The question is which Terraform root manages the role's permissions.

If `envs/prod` managed them, the role applying `envs/prod` would need permission to edit its own policies. Any change merged to `main` could then grant the pipeline wider access, up to full control of the account, which also holds other projects.

## Decision

- **Bootstrap owns the CI access.** `infra/modules/ci_role` defines the CI roles and their permissions. It is applied only from `infra/bootstrap`, by a human, as `mtd-admin`.
- **Two roles:**
  - `mtd-ci-plan`: assumable only by `pull_request` jobs. It is read-only and plans without a state lock.
  - `mtd-ci-apply`: assumable only by jobs in the `production` environment. That environment is restricted to `main` and needs a reviewer.
- **The pipeline can manage only `mind-the-data-*`.** That covers resources named `mind-the-data-*`, and Glue databases named `mind_the_data_*`.
- **Its own access is out of reach.** The CI roles and the boundary policy are named `mtd-*`, outside the prefix the pipeline can manage, so it cannot edit them.
- **Workload roles carry a boundary.** Every IAM role the pipeline creates must carry the `mtd-workload-boundary` permissions boundary. The pipeline cannot remove it.
- **Three repository variables:** `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN` and `AWS_ROLE_ARN`. The original single `AWS_ROLE_ARN` is not enough for two roles.

## Alternatives considered

- **`ci_role` module applied from `envs/prod`.** This places it where the original task wording suggests, but it lets the pipeline escalate its own access.
- **A single role for both plan and apply.** Pull requests would then hold write credentials, and the environment approval would protect nothing.
- **The AWS managed `ReadOnlyAccess` policy for plans.** It would let pull request jobs read every object in the account, including other projects' data.

## Consequences

- **New permissions need a human.** When a milestone adds a new kind of resource, the permission change goes in `infra/modules/ci_role` and Karsh re-applies the bootstrap. The plan in CI will fail with AccessDenied until then. That is noisy, but safe.
- **Plans can't lock state.** Pull request plans run with `-lock=false`, because the plan role cannot write the lock file.
- **The approval gate depends on the GitHub plan.** It relies on the `production` environment's protection rules, and on a private repository under GitHub Free those are not enforced. See the runbook, H7.
