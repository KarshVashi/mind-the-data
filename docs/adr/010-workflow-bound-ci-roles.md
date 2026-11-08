# ADR-010: CI roles are bound to workflow files

Date: 2026-11-07
Status: proposed

## Context

ADR-005 lists an accepted risk: any job running on `main` can assume the apply role. The OIDC subject is the same for every workflow on the default branch (`repo:KarshVashi@41749592/mind-the-data@1442302649:ref:refs/heads/main`), so a new workflow merged to `main` with `id-token: write` could take deploy credentials without touching infra code. The transform role has the same exposure. More scheduled workflows are coming (health checks now, the weekly publish in M5), which makes this more likely.

ADR-005 suggested customising the repository's OIDC subject template so that `sub` includes `job_workflow_ref`. That was the usual approach while IAM could only match `sub`, `aud` and `amr`. Two sources were checked before choosing:

- **AWS IAM** ([condition keys for OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_iam-condition-keys.html#condition-keys-wif), GitHub tab) now maps GitHub claims to their own trust-policy condition keys: `job_workflow_ref`, `repository_id`, `repository_owner_id`, `ref`, `workflow`, `environment`, `actor_id` and `enterprise_id`. They can be used in the trust policy, though not later in the session. Its example policy uses them alongside the `sub` check that IAM requires for GitHub.
- **GitHub** ([OIDC reference](https://docs.github.com/en/actions/reference/security/oidc); REST `PUT /repos/{owner}/{repo}/actions/oidc/customization/sub`). A custom template (`use_default: false`, `include_claim_keys: ["repo", "context", "job_workflow_ref"]`) replaces the default `sub` with `repo:…:<context>:job_workflow_ref:<owner>/<repo>/.github/workflows/<file>@<ref>`. With immutable subjects, `owner_id` and `repo_id` stay in the `repo` segment whatever the template. The PUT also takes `use_immutable_subject`, which should be sent as `true` explicitly. The template changes `sub` for every workflow in the repository at once.

## Decision

Keep the subject template at its default. Add a `token.actions.githubusercontent.com:job_workflow_ref` condition beside the existing `sub` condition in each role's trust policy (`infra/modules/ci_role`).

| Role | `sub` context | Workflow files (`job_workflow_ref`) |
| --- | --- | --- |
| `mtd-ci-plan` | `pull_request` | `infra.yml`, `bootstrap-plan.yml` at `refs/pull/*/merge` (`StringLike`) |
| `mtd-ci-plan` | `ref:refs/heads/main` | `bootstrap-plan.yml` (weekly drift check) |
| `mtd-ci-apply` | `ref:refs/heads/main` | `infra.yml` |
| `mtd-ci-transform` | `ref:refs/heads/main` | `transform-daily.yml` |
| `mtd-ci-observer` | `ref:refs/heads/main` | `ops-daily.yml`, `ops-daytime.yml` |
| `mtd-ci-publish` | `ref:refs/heads/main` | `publish-weekly.yml` |

The `job_workflow_ref` value is `KarshVashi/mind-the-data/.github/workflows/<file>@<ref>`. It names the repository by name, but the `sub` condition in the same statement pins the repository by id, so the pair cannot be matched from another repository.

### Migration without an outage

The claim's form is checked before any existing role depends on it.

1. **First apply** (this change). Trust that is new is bound from the start: the observer and publish roles, and the plan role on `main`. If the claim did not match, only those new paths would fail. The roles already in use (plan on pull requests, apply, transform) keep their current trust until `enforce_workflow_binding` is turned on.
2. **Probe.** Run `bootstrap-plan.yml` by hand on `main` with `trust_probe=new-roles`.
   - Its `plan` job succeeding proves AWS reads the claim and that its value is exactly `…/bootstrap-plan.yml@refs/heads/main`. With the condition key missing or different, `StringEquals` would refuse.
   - The `trust-probe` job runs in one place.
     - It is the positive control: the plan role must accept the token.
     - It is also the negative check: the observer and publish roles must refuse the same token.
     - STS reports a missing role the same way as an untrusted one, so the job fails if any expected role variable is unset.
   - The `oidc-claims` job prints `sub` and `job_workflow_ref` for each kind of run. On the follow-up pull request it shows the `refs/pull/<n>/merge` form, which has to be checked by eye before step 3: a run on `main` cannot exercise the plan role's pull request binding.
3. **Second apply** (a one-line follow-up change).
   1. Set `enforce_workflow_binding` to `true` and re-apply.
   2. Run the probe with `trust_probe=all-roles`.
   3. Confirm that an `infra.yml` plan on a pull request still assumes the plan role, and that `transform-daily.yml` and the next `infra.yml` run on `main` still assume theirs.

**Progress.** Step 1 was applied on 2026-11-08. The step 2 probe passed on `main` the same day (run 42434755955: `plan`, `oidc-claims` and `trust-probe` green). The pull request form was read from #22's `oidc-claims` job: `KarshVashi/mind-the-data/.github/workflows/bootstrap-plan.yml@refs/pull/22/merge`, which matches `@refs/pull/*/merge`. Step 3 is the change that sets `enforce_workflow_binding` to `true` by default in `infra/bootstrap/variables.tf`; its apply, the `all-roles` probe and the role checks are owner steps (runbook H6, step 5).

If step 2 fails, leave `enforce_workflow_binding` off and use the subject-template route below. The new roles would need the template's `sub` form, which is a bootstrap change. Nothing in use breaks in the meantime.

## Alternatives considered

- **Customise the subject template to include `job_workflow_ref`.** It needs a GitHub API call by the owner. During migration every role has to trust both the old and new `sub` forms, and a third step drops the old form. It also changes `sub` for every workflow at once. It remains the fallback if AWS stops evaluating the claim key.
- **Match the `workflow` claim (the workflow's display name).** Any file can declare any name, so this proves nothing.
- **One role per workflow, still bound by `sub` only.** Separate roles don't help when they all trust the same subject.

## Consequences

- **The ADR-005 risk is closed.** After step 3, a new workflow on `main` cannot assume any CI role it isn't listed for.
- **Renaming a bound workflow file needs a bootstrap re-apply.** The file names in `infra/modules/ci_role` (`workflow_files`) are a contract with `.github/workflows/`. Moving a job into a reusable workflow changes `job_workflow_ref` to the reusable file, so the change has to be made there too.
- **Pull request runs edit their own workflow file.** Binding the plan role on pull requests stops other workflows from using it, but a pull request can still rewrite `infra.yml` itself. That's acceptable because the plan role is read-only, as it was before.
- **The plan role now also runs on `main`,** only from `bootstrap-plan.yml`, for the weekly drift check. It stays read-only.
- **Two repository variables:** `AWS_OBSERVER_ROLE_ARN` and `AWS_PUBLISH_ROLE_ARN`. `scripts/bootstrap.sh apply` sets them.
- **Revisit** if GitHub or AWS change how `job_workflow_ref` is issued or evaluated. The probe is the check.
