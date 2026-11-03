# Runbook

Commands for the steps a person has to do. Run them from the repository root unless stated. Region is `eu-west-2` throughout. Admin commands pass `--profile mtd-admin` or set it inline, so the admin profile never stays set in your shell.

The rest of the runbook (alarms, re-runs, backfills, key rotation in depth) arrives with M1 and M7.

---

## H6: Bootstrap (once)

Creates the remote state bucket and the two CI roles (read-only plan for pull requests, deploy for `main`), and wires them to the existing GitHub OIDC provider. Run it as `mtd-admin` with MFA, never from CI.

**1. Block public S3 access for the whole account.** It is currently off. This is the strongest guard against a bucket being made public by mistake.

```sh
ACCOUNT_ID=$(aws sts get-caller-identity --profile mtd-admin --query Account --output text)
aws s3control put-public-access-block --profile mtd-admin --account-id "$ACCOUNT_ID" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

This applies to every bucket in the account, including other projects'. Check first that none of them is meant to be public.

**2. Check whether the account already has a GitHub OIDC provider.** An account can have only one.

```sh
aws iam list-open-id-connect-providers --profile mtd-admin
```

If the output lists `token.actions.githubusercontent.com`, keep the default. If it is empty, add `-var create_github_oidc_provider=true` to the plan command in step 3.

**3. Plan, review, apply.**

```sh
AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap init
AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
#   Expect: 1 S3 bucket and its settings, 1 IAM policy (mtd-workload-boundary),
#   2 IAM roles (mtd-ci-plan, mtd-ci-apply) with inline policies, and an OIDC
#   provider only if you asked for one. Nothing else.
AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap apply bootstrap.tfplan
```

**4. Keep the bootstrap state safe.** It is local by design and is gitignored. Copy it into the state bucket so it survives a lost laptop:

```sh
BUCKET=$(terraform -chdir=infra/bootstrap output -raw state_bucket)
aws s3 cp --profile mtd-admin infra/bootstrap/terraform.tfstate "s3://$BUCKET/bootstrap/terraform.tfstate"
```

CI can read and write only `envs/` in that bucket, so it cannot touch this copy. Repeat the copy after every bootstrap apply.

**5. Set the repository variables.** These are identifiers, not secrets.

```sh
gh variable set TF_STATE_BUCKET   --body "$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
gh variable set AWS_PLAN_ROLE_ARN --body "$(terraform -chdir=infra/bootstrap output -raw plan_role_arn)"
gh variable set AWS_ROLE_ARN      --body "$(terraform -chdir=infra/bootstrap output -raw apply_role_arn)"
```

From now on:

- **Pull requests** that touch `infra/` get a read-only plan.
- **Merging** such a pull request deploys it (see H10).

**Re-applying.** When a later milestone needs the CI roles to manage a new kind of resource, the change lands in `infra/modules/ci_role` and you re-run steps 3 and 4. CI cannot change its own permissions.

---

## H7: Merge settings

Merging a pull request into `main` is what deploys infrastructure (ADR-005). In the repository's Settings → General → Pull Requests:

- Allow squash merging only.
- Automatically delete head branches: on.

Nothing else is needed. No GitHub environment is used. The deploy role trusts only jobs running on `main`, so it works the same on a free private repository.

**Only you can deploy.** Today only you have write access to the repository, so only your merges or pushes to `main` can deploy. Before giving anyone else write access, revisit ADR-005.

**Deploying from your machine.** Use this only if CI cannot deploy, for example when GitHub Actions is down. Plan, check the plan against the merged pull request, then apply:

```sh
TF_STATE_BUCKET=$(terraform -chdir=infra/bootstrap output -raw state_bucket)
AWS_PROFILE=mtd-admin terraform -chdir=infra/envs/prod init -backend-config="bucket=$TF_STATE_BUCKET"
AWS_PROFILE=mtd-admin terraform -chdir=infra/envs/prod plan -out=prod.tfplan
AWS_PROFILE=mtd-admin terraform -chdir=infra/envs/prod apply prod.tfplan
```

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

Rulesets are enforced on public repositories on every plan. On a private repository they need a paid plan.

---

## H10: Approve an infrastructure change

Merging is the approval.

1. **Review the plan.** On the pull request, the `infra` workflow's `plan` job writes the plan to its run summary. Read it there.
2. **Merge.** Merge only if the plan shows the changes the pull request says it makes, and nothing else.
3. **Watch the deploy.** The merge starts the `deploy` job on `main`. It plans again from `main` and applies that plan. It also writes the plan it applied to its run summary, so you can check it matches what you reviewed.

If `main` moved after the pull request's plan ran, the deploy includes those changes too. Merging soon after the pull request's plan keeps the two the same.

**Deletes and replacements are blocked by default.**

- The pull request's plan flags them with a warning.
- The deploy fails if its plan deletes or replaces any resource, before anything is changed.
- To allow it, put `[allow-destroy]` in the pull request title before merging. The title is always part of the squash commit message.
- Or re-run the `infra` workflow by hand on `main` with `allow_destroy` ticked.

---

## Planning locally

```sh
export AWS_PROFILE=mtd-dev   # read-only
export TF_STATE_BUCKET=<state_bucket output>
make plan
```

`make plan` runs without a state lock, because the dev profile is read-only.
