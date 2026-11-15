# ADR-011: The engineer may merge low-risk pull requests through a gate

Date: 2026-11-08
Status: accepted (owner, 2026-11-15, D15)

## Context

The working rules say the engineer never merges a pull request; the owner does. Merging into `main` is also the deploy approval (ADR-005). Every pull request therefore waits for the owner, including docs-only and test-only changes that cannot deploy anything or cost anything.

The owner wants changes that cost well under $1 to deploy to merge without them. Cost cannot be measured before a deploy. A Terraform plan shows which resources change and how, but not what they will cost. A change to Lambda code costs nothing to deploy, while a memory bump on the per-minute poller could cost tens of dollars a month.

The repository is private on GitHub Free, so rulesets and required reviewers are not enforced. Any gate is a process guard run by the engineer, not something GitHub enforces.

## Decision

The engineer may merge a pull request only through `make merge PR=<n>`, and only when the gate in `scripts/merge_gate.py` (logic in `src/mind_the_data/ops/merge_gate.py`, rules in `config/merge_policy.yaml`) returns ALLOW. Everything else still waits for the owner. The gate uses stand-ins for cost:

- **Changed paths.** Protected paths always need the owner: the bootstrap and the `ci_role` module, `.github/**`, `docs/adr/**`, the gate's own files, and anything named for budgets or quotas. In the Makefile, only the marked merge gate block and lines that could change how it runs are protected.
- **The PR's own CI plan.** When anything deployable changes, the plan job's `plan-changes` artifact for the head commit must exist. It must only create or update allowlisted resource types. Never IAM, never a delete, replace or import, never an unlisted type. It must come from a `pull_request` run of the infra workflow. The artifact holds addresses, modes, types and actions only, with no attribute values. A local plan is not accepted, because it differs from CI's (no alert email, a different zip hash).
- **CI and identity.** All checks on the head commit must have finished, and none failed. `ci` must succeed, and `infra / plan` too when deployable paths changed. The pull request must not be a draft, and its author must be on the allowlist. No `[allow-destroy]` marker may appear in its title, body or commits.
- **Pinning.** The head SHA is read once and the merge uses `--match-head-commit`.
- **Budget.** Budget and quota files and resource types are protected, so the AWS Budget alerts stay in place as the backstop after a merge.

The gate always runs `refs/remotes/origin/main`'s copy of its code, policy and lock file, fetched without tags, exported to a temporary directory and run with `python -I`. A feature branch cannot loosen its own gate. Changing the gate means changing protected files, so it needs the owner.

## Alternatives considered

- **Keep owner-only merging.** This is the safest option, but it is also the bottleneck the owner asked to remove.
- **GitHub auto-merge with required checks.** Rulesets are not enforced on a private Free repository, and auto-merge cannot inspect a plan's resource types.
- **Estimate cost from the plan (Infracost or similar).** This adds a dependency and an API key. It still cannot see usage-driven cost, such as a Lambda memory change multiplied by invocations, so it would only be a better guess.
- **Allow by path only.** That is simpler, but any `src/` change redeploys Lambdas. Without the plan, a change that touches IAM through a module would go unseen.

## Consequences

- Docs, tests, dbt models and code-only Lambda changes can land without waiting for the owner. Each merge has a printed verdict.
- Accepted risks:
  - An allowed type can still raise cost. A Lambda memory or schedule rate change is one example. The budget alerts catch it after the fact, not before.
  - The plan job runs `make package` from the pull request before Terraform. A hostile Makefile could therefore tamper with the evidence. The gate trusts the engineer not to be hostile. It guards against mistakes.
  - `pyproject.toml` and `uv.lock` are not protected, so dependency bumps can pass. The gate's environment is built from `main`'s copies, so a gated merge that changes `[tool.uv]` sources or the pydantic or pyyaml pins changes the gate's next run. That is a two-step loosening. A hashed requirements file under a protected path would close it.
  - The `make merge` recipe runs from the checked-out Makefile. It refuses when its block differs from the exported copy, but a deliberately edited recipe could skip that. The runbook says to run it from `main`.
  - `transform/**` (dbt models and seeds) is neither protected nor plan-checked. A gated merge can change published metrics. Its cost is capped by the Athena workgroup's scan limit, but its correctness is not.
  - The plan is of the merge with `main` at the time it ran. If `main` moves, the deploy re-plans, and the destroy guard still blocks deletes.
- The gate does not stop anyone running `gh pr merge` directly. It is a convention with tooling, not an access control.
- Revisit this when the repository moves to a plan with enforced rulesets, when someone else gets write access, or after the first month's cost report (M7).
