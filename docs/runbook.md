# Runbook

Commands for the steps a person has to do. Run them from the repository root unless stated. Region is `eu-west-2` throughout. Admin commands pass `--profile mtd-admin` or set it inline, so the admin profile never stays set in your shell.

The rest of the runbook (alarms, re-runs, backfills, key rotation in depth) arrives with M1 and M7.

---

## H6: Bootstrap (once)

Creates the remote state bucket and the read-only CI plan role, and wires them to the existing GitHub OIDC provider. Run it as `mtd-admin` with MFA, never from CI.

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
#   1 IAM role (mtd-ci-plan) with an inline policy, and an OIDC provider only
#   if you asked for one. No apply role yet (see H7).
AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap apply bootstrap.tfplan
```

**4. Keep the bootstrap state safe.** It is local by design and is gitignored. Copy it into the state bucket so it survives a lost laptop:

```sh
BUCKET=$(terraform -chdir=infra/bootstrap output -raw state_bucket)
aws s3 cp --profile mtd-admin infra/bootstrap/terraform.tfstate "s3://$BUCKET/bootstrap/terraform.tfstate"
```

CI can read and write only `envs/` in that bucket, so it cannot touch this copy. Repeat the copy after every bootstrap apply.

**5. Set the plan variables.** These are identifiers, not secrets.

```sh
gh variable set TF_STATE_BUCKET   --body "$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
gh variable set AWS_PLAN_ROLE_ARN --body "$(terraform -chdir=infra/bootstrap output -raw plan_role_arn)"
```

Pull requests that touch `infra/` now get a `terraform plan`. Nothing is applied from CI until H7.

**Re-applying.** When a later milestone needs the CI roles to manage a new kind of resource, the change lands in `infra/modules/ci_role` and you re-run steps 3 and 4. CI cannot change its own permissions.

---

## H7: `production` environment and the apply role

The apply role trusts any job that names the `production` environment. It is only safe once that environment's protection rules are enforced. On a private repository, that needs a paid GitHub plan; a public repository has them for free.

**1. Merge settings.** In the repository's Settings → General → Pull Requests:

- Allow squash merging only.
- Automatically delete head branches: on.

**2. If the protection rules can be enforced (public repository or paid plan):**

1. In Settings → Environments, create `production` and set:
   - **Required reviewers:** you.
   - **Prevent self-review:** off.
   - **Deployment branches and tags:** Selected branches, rule `main`.
2. Check that the rules show as active, with no banner saying they are unavailable on your plan.
3. Record the setting locally, so every later bootstrap apply keeps the role. The file is gitignored.

   ```sh
   echo 'enable_apply_role = true' >> infra/bootstrap/terraform.tfvars
   ```

4. Create the apply role and set its variable:

   ```sh
   AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
   #   Expect: 1 IAM role (mtd-ci-apply) and 2 inline policies added. Nothing else.
   AWS_PROFILE=mtd-admin terraform -chdir=infra/bootstrap apply bootstrap.tfplan
   gh variable set AWS_ROLE_ARN --body "$(terraform -chdir=infra/bootstrap output -raw apply_role_arn)"
   ```

5. Repeat H6 step 4 to back up the state.

The uploaded plan artifact holds unredacted values, such as the alarm email address. It is kept for one day. Anyone with read access to the repository can download it during that time, which matters if the repository is public.

**3. If they cannot be enforced (private repository on GitHub Free):** leave the apply role disabled and `AWS_ROLE_ARN` unset. Deploy `infra/envs/prod` from your machine after review:

```sh
TF_STATE_BUCKET=$(terraform -chdir=infra/bootstrap output -raw state_bucket)
AWS_PROFILE=mtd-admin terraform -chdir=infra/envs/prod init -backend-config="bucket=$TF_STATE_BUCKET"
AWS_PROFILE=mtd-admin terraform -chdir=infra/envs/prod plan -out=prod.tfplan
#   Compare with the plan shown on the merged pull request.
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

On a private repository under GitHub Free, rulesets are not enforced. The same plan note as H7 applies.

---

## H10: Approve a production apply

A merge to `main` that touches `infra/` starts the `infra` workflow.

1. **Plan.** The `plan` job runs first, without approval, and writes the plan to the run summary.
2. **Review.** The `apply` job then waits on the `production` environment. Read the plan in the summary, or in the `plan` job's log.
3. **Approve.** Approve only if the plan shows the changes the pull request said it would make.

The apply runs exactly the saved plan. If anything changed state in between, Terraform rejects the plan as stale and nothing is applied.

---

## Planning locally

```sh
export AWS_PROFILE=mtd-dev   # read-only
export TF_STATE_BUCKET=<state_bucket output>
make plan
```

`make plan` runs without a state lock, because the dev profile is read-only.
