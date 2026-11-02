# Runbook

Commands for the steps a person has to do. Run them from the repository root unless stated. Region is `eu-west-2` throughout.

The rest of the runbook (alarms, re-runs, backfills, key rotation in depth) arrives with M1 and M7.

---

## H6: Bootstrap (once)

Creates the remote state bucket and the CI roles, and wires the existing GitHub OIDC provider to them. Run it as the admin profile (`mtd-admin`, with MFA), never from CI.

**1. Check whether the account already has a GitHub OIDC provider.** An account can have only one.

```sh
aws iam list-open-id-connect-providers --profile mtd-admin
```

If the output lists `token.actions.githubusercontent.com`, keep the default (`create_github_oidc_provider = false`). If it is empty, add `-var create_github_oidc_provider=true` to the plan command in step 2.

**2. Plan, review, apply.**

```sh
export AWS_PROFILE=mtd-admin
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
#   Expect: 1 S3 bucket and its settings, 1 IAM policy (mtd-workload-boundary),
#   2 IAM roles (mtd-ci-plan, mtd-ci-apply) with inline policies, and an OIDC
#   provider only if you asked for one. Nothing else.
terraform -chdir=infra/bootstrap apply bootstrap.tfplan
terraform -chdir=infra/bootstrap output
```

**3. Keep the bootstrap state safe.** It is local by design and is gitignored. Copy it into the state bucket so it survives a lost laptop:

```sh
BUCKET=$(terraform -chdir=infra/bootstrap output -raw state_bucket)
aws s3 cp infra/bootstrap/terraform.tfstate "s3://$BUCKET/bootstrap/terraform.tfstate"
```

CI can read and write only `envs/` in that bucket, so it cannot touch this copy.

**4. Set the repository variables.** CI reads these. They are identifiers, not secrets.

```sh
gh variable set TF_STATE_BUCKET   --body "$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
gh variable set AWS_PLAN_ROLE_ARN --body "$(terraform -chdir=infra/bootstrap output -raw plan_role_arn)"
gh variable set AWS_ROLE_ARN      --body "$(terraform -chdir=infra/bootstrap output -raw apply_role_arn)"
```

Do H7 before setting `AWS_ROLE_ARN`. The apply job starts as soon as that variable exists, and GitHub creates a missing environment automatically with no protection rules.

**Re-applying.** When a later milestone needs the CI roles to manage a new kind of resource, the change lands in `infra/modules/ci_role` and you re-run step 2. CI cannot change its own permissions.

---

## H7: `production` environment and merge settings

**Environment.** In the repository on GitHub, go to Settings → Environments → New environment and name it `production`. Then set:

- **Required reviewers:** you.
- **Prevent self-review:** off. You are the only reviewer.
- **Deployment branches and tags:** Selected branches, rule `main`.

**Merge settings.** In Settings → General → Pull Requests:

- Allow squash merging only.
- Automatically delete head branches: on.

**GitHub plan.** Required reviewers on environments, and enforced rulesets, need either a public repository or a paid GitHub plan. On a private repository under GitHub Free, the `production` environment will not wait for approval. In that case:

- Leave `AWS_ROLE_ARN` unset until the repository is public or the plan allows the gate.
- Deploy infra with `make plan` and a reviewed local apply as `mtd-admin` instead.

---

## H8: TfL app key in SSM

Register at the TfL API portal, then store the primary key. `read -s` keeps the key out of shell history and off the screen.

```sh
read -rs TFL_APP_KEY && \
aws ssm put-parameter \
  --profile mtd-admin --region eu-west-2 \
  --name /mind-the-data/tfl/app_key \
  --type SecureString \
  --value "$TFL_APP_KEY" \
  --tags Key=project,Value=mind-the-data ; \
unset TFL_APP_KEY
```

Check that it exists without printing it:

```sh
aws ssm describe-parameters --profile mtd-admin --region eu-west-2 \
  --parameter-filters Key=Name,Values=/mind-the-data/tfl/app_key \
  --query 'Parameters[].{name:Name,type:Type,version:Version}'
```

**Rotating the key.** Run the same command with `--overwrite` in place of `--tags` (the two cannot be combined). The poller reads the key at cold start, so new invocations pick it up within minutes.

---

## H9: Require the `ci` check on `main`

After the first green `ci` run on a pull request:

1. Open the `main` ruleset.
2. Enable "Require status checks to pass".
3. Add `ci`.
4. Enable "Require branches to be up to date".

The same GitHub plan note as H7 applies.

---

## H10: Approve a production apply

A merge to `main` that touches `infra/` starts the `infra` workflow. Its `apply` job waits on the `production` environment.

1. Open the run.
2. Read the plan step's output.
3. Approve only if the plan shows the changes the pull request said it would make.

---

## Planning locally

```sh
export AWS_PROFILE=mtd-dev   # read-only
export TF_STATE_BUCKET=<state_bucket output>
make plan
```

`make plan` runs without a state lock, because the dev profile is read-only.
