# Runbook

Commands for the steps a person has to do. Run them from the repository root unless stated. Region is `eu-west-2` throughout.

Operating it day to day: [what each alarm means and what to do](#alarms-what-to-do), [re-running a workflow](#re-running), [backfilling the marts from raw](#backfilling-the-marts-from-raw), [a full refresh in prod](#full-refresh-in-prod), [Iceberg maintenance](#iceberg-maintenance), [the monthly open data](#monthly-open-data) and [rotating the TfL key](#rotating-the-tfl-key). Costs are in [cost.md](cost.md).

---

## H3: Admin identity

Admin work runs through IAM Identity Center. You sign in through your browser with MFA, and get short-lived credentials for an `AdministratorAccess` permission set. There are no long-lived access keys.

**1. Secure the root user.** Sign in as root, then open the account menu (top right) → Security credentials.

- Under "Multi-factor authentication", assign an MFA device (a passkey or an authenticator app).
- Under "Access keys", there should be none. Delete any that exist.
- After this, don't use root for day-to-day work.

**2. Make sure the Identity Center user exists.** Go to IAM Identity Center → Users. The user `mtd-admin` should exist, with an MFA device.

**3. Create the admin permission set.** Go to IAM Identity Center → Multi-account permissions → Permission sets → Create permission set.

- **Type:** predefined, `AdministratorAccess`.
- **Session duration:** 1 hour.

**4. Assign it.** Go to IAM Identity Center → Multi-account permissions → AWS accounts.

1. Tick this account → Assign users or groups.
2. Pick the user `mtd-admin`.
3. Pick the permission set `AdministratorAccess` → Submit.

**5. Configure the CLI.** Note the AWS access portal URL (IAM Identity Center → Dashboard → Settings summary) and the region Identity Center runs in (Settings). Then:

```sh
aws configure sso
#   SSO session name:     mtd
#   SSO start URL:        <AWS access portal URL>
#   SSO region:           <Identity Center region>
#   Registration scopes:  (press Enter)
#   Browser opens: sign in as mtd-admin with MFA, then allow access.
#   Choose this account, then the AdministratorAccess role.
#   Default client region: eu-west-2
#   Output format:         json
#   Profile name:          mtd-admin
```

**6. Test.**

```sh
aws sso login --sso-session mtd     # browser sign-in with MFA; lasts the session duration
aws sts get-caller-identity --profile mtd-admin
```

The output should show an `assumed-role/AWSReservedSSO_AdministratorAccess_.../mtd-admin` ARN.

**Admin credentials for Terraform.** After `aws sso login`, either run Terraform with `AWS_PROFILE=mtd-admin`, or export the credentials for the shell and clear them afterwards:

```sh
eval "$(aws configure export-credentials --profile mtd-admin --format env)"
# ... terraform commands ...
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION
```

---

## H6: Bootstrap

`infra/bootstrap` holds everything CI must not be able to change for itself:

- the remote state bucket and the GitHub OIDC provider
- the CI roles and the workload boundary. The roles are `mtd-ci-plan`, `mtd-ci-apply`, `mtd-ci-transform`, `mtd-ci-observer` and `mtd-ci-publish`, each bound to its workflow files (ADR-010).
- the AWS Budget, the `project` cost allocation tag and the Lambda concurrency quota

Only you apply it, as `mtd-admin` through Identity Center (H3), never from CI. The make targets run it from a temporary `git worktree` of `origin/main` under the system temp directory, and remove that worktree afterwards. Your checkout never switches branch.

Steps 1 and 2 are for a new account only. They were done on 2026-11-03.

**1. Block public S3 access for the whole account.** It is currently off. This is the strongest guard against a bucket being made public by mistake.

First check that no existing bucket relies on being public. This lists each bucket and whether AWS considers its policy public:

```sh
for b in $(aws s3api list-buckets --profile mtd-admin --query 'Buckets[].Name' --output text); do
  printf '%s\t' "$b"
  aws s3api get-bucket-policy-status --profile mtd-admin --bucket "$b" \
    --query PolicyStatus.IsPublic --output text 2>/dev/null || echo "no policy"
done
```

If any bucket shows `True`, or serves a public website, decide about it before turning the block on.

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

If the output lists `token.actions.githubusercontent.com`, keep the default. If it is empty, prefix the make commands in step 3 with `TF_VAR_create_github_oidc_provider=true`.

**3. Plan, review, apply.** Run these from your normal checkout, on any branch. They run what is on `origin/main`, so merge the change first.

```sh
make bootstrap-plan    # read-only plan; signs in with `aws sso login` if needed
make bootstrap-apply   # plans again, shows it, applies after you type yes
```

`make bootstrap-apply` applies exactly the plan it showed. It then sets the repository variables from the outputs: `AWS_ACCOUNT_ID`, `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN`, `AWS_ROLE_ARN`, `AWS_TRANSFORM_ROLE_ARN`, `AWS_OBSERVER_ROLE_ARN`, `AWS_PUBLISH_ROLE_ARN` and `BOOTSTRAP_PLAN_ENABLED`. These are identifiers, not secrets. Until `AWS_ACCOUNT_ID` or the explicit variables are set, the `infra` and `transform-daily` workflows skip with a notice (see "Repository variables" below).

The budget alert address comes from the `ALERT_EMAIL` repository variable, read with `gh`. To use a different one, set `ALERT_EMAIL=you@example.com` before the command. Set `BOOTSTRAP_PROFILE` or `BOOTSTRAP_REF` to change the profile (default `mtd-admin`) or the ref (default `origin/main`).

**4. State.** The bootstrap state is at `s3://mtd-tfstate-<account>/bootstrap/terraform.tfstate`, locked with an S3 lockfile. The bucket is versioned, so earlier versions are kept for 90 days. CI's plan role can read this key (for the drift check). Only you can write it: the apply role can write `envs/` only.

*One-time move from local state.* Until 2026-11 the state was a local file in `infra/bootstrap/`, with copies uploaded by hand. After the change that adds the S3 backend is merged:

```sh
make bootstrap-migrate-state   # copies infra/bootstrap/terraform.tfstate into the bucket; applies nothing
make bootstrap-plan            # must show only the changes you expect, not a rebuild
```

`migrate-state` refuses if the copy in the bucket is newer than the local file. Once a plan reads the state from S3 correctly, the local `infra/bootstrap/terraform.tfstate*` files are no longer used. Keep them a week, then delete them.

*Rebuilding in a new account.* The backend bucket is created by this same root, so the first apply has to use local state. Do these steps in the checkout, not through the make targets, and don't commit the override file (it is gitignored):

```sh
printf 'terraform {\n  backend "local" {}\n}\n' > infra/bootstrap/backend_override.tf
AWS_PROFILE=mtd-admin TF_VAR_alert_email=you@example.com terraform -chdir=infra/bootstrap init
AWS_PROFILE=mtd-admin TF_VAR_alert_email=you@example.com terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
# review, then apply bootstrap.tfplan with the same environment
rm infra/bootstrap/backend_override.tf
make bootstrap-migrate-state
```

**5. Bind the roles to their workflows (ADR-010).** Do this once, after the first apply that includes `mtd-ci-observer`. Steps 1 and 2 were done on 2026-11-08: the `new-roles` probe passed, and the pull request form matched. Step 3 is the change that makes `enforce_workflow_binding` default to `true`; steps 4 to 7 follow its merge.

1. Run the trust probe:

   ```sh
   gh workflow run bootstrap-plan.yml --ref main -f trust_probe=new-roles
   gh run watch "$(gh run list --workflow bootstrap-plan.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```

   All of its jobs must pass:
   - `plan` and `trust-probe` show that the plan role accepts `bootstrap-plan.yml` on `main`, and that the observer and publish roles refuse it.
   - `oidc-claims` writes the claims into the run summary.
2. Check the pull request form. Open any pull request that runs `bootstrap-plan.yml` (the form does not depend on which one; #22's was used) and look at the summary of its `bootstrap-plan / oidc-claims` job. `job_workflow_ref` must read `KarshVashi/mind-the-data/.github/workflows/bootstrap-plan.yml@refs/pull/<number>/merge`, which is the form the plan role will match on pull requests. If it reads differently, stop and fix `plan_pull_request` in `infra/modules/ci_role/main.tf` first.
3. Merge the follow-up change that sets `enforce_workflow_binding` to `true` in `infra/bootstrap/variables.tf`.
4. Run `make bootstrap-apply` again.
5. Run the probe again with `-f trust_probe=all-roles`.
6. Open or update any pull request that touches `infra/`. Its `infra / plan` job must still assume the plan role.
7. Check the two main-branch roles this rebinds. Run the transform and watch it get past "configure AWS credentials"; for the apply role, watch the next `infra` run on `main` do the same:

   ```sh
   gh workflow run transform-daily.yml --ref main
   gh run watch "$(gh run list --workflow transform-daily.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```

If step 1 or 2 fails, stop. Leave `enforce_workflow_binding` off and read ADR-010's fallback.

If step 5, 6 or 7 fails after the apply, a pull request plan, a deploy or the transform can no longer assume its role. Recover from your machine: `TF_VAR_enforce_workflow_binding=false make bootstrap-apply` restores the unbound trust for the plan (pull request), apply and transform roles. Until a fix merges, every `make bootstrap-apply` needs that prefix, or it binds the roles again, and the Monday drift check opens a "Bootstrap drift" issue. Then open a change that sets the default back to `false`, removes `test_bootstrap_binds_the_pre_adr_010_roles` from `tests/unit/test_infra_invariants.py`, and says why in ADR-010.

From now on:

- **Pull requests** that touch `infra/` get a read-only plan of `infra/envs/prod`.
- Pull requests that touch `infra/bootstrap/` or `infra/modules/ci_role/` also get a bootstrap plan (`bootstrap-plan.yml`).
- **Merging** such a pull request deploys `infra/envs/prod` (see H10). The bootstrap is never deployed by a merge.

**Drift check.** Every Monday `bootstrap-plan.yml` plans the bootstrap on `main`. If the plan is not empty, it opens an issue titled "Bootstrap drift: plan is not empty", or comments on the open one. A change made by hand in the console, such as to a CI role or the budget, shows up here.

**Money and quota.** All three are set by the bootstrap:

- **Budget.** `mind-the-data-monthly` is 6.50 USD a month, about the £5 in plan H5; Budgets works in USD. It emails `ALERT_EMAIL` at 50%, 80% and 100% of actual spend, and when forecast spend passes 100%. It counts only costs tagged `project = mind-the-data`, from the day that tag was activated.
- **Cost allocation tag.** `project` is activated by `aws_ce_cost_allocation_tag`. This only works once the key has appeared in billing data, which it has. Costs are attributed from activation onwards, and it can take up to 24 hours to show.
- **Lambda concurrency quota.** `aws_servicequotas_service_quota.lambda_concurrency` holds the quota at 1,000 (see "Lambda concurrency quota" below). It is adopted with an `import` block, so the first apply sends no request.

**Re-applying.** When a later milestone needs the CI roles to manage a new kind of resource, the change lands in `infra/modules/ci_role`. Merge it, then run `make bootstrap-plan` and `make bootstrap-apply`. CI cannot change its own permissions. These are already granted for M3 to M7, so those milestones should not need a re-apply for them:

- CloudWatch dashboards, composite alarms and Lambda layers named `mind-the-data-*`
- configuration parameters under `/mind-the-data/config/`
- the publish role

---

## Repository variables

`AWS_ACCOUNT_ID` is the only variable the workflows need. Everything else follows the naming convention in `infra/`. `make bootstrap-apply` (H6, step 3) sets `AWS_ACCOUNT_ID` and the first five rows below from the bootstrap outputs; the `DBT_ATHENA_*_PROD` values are only derived:

| Variable | Derived from `AWS_ACCOUNT_ID` | Used by |
| --- | --- | --- |
| `TF_STATE_BUCKET` | `mtd-tfstate-<id>` | infra |
| `AWS_PLAN_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-plan` | infra (pull requests) |
| `AWS_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-apply` | infra (main) |
| `AWS_TRANSFORM_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-transform` | transform-daily |
| `AWS_OBSERVER_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-observer` | ops-daily, ops-daytime |
| `DBT_ATHENA_S3_STAGING_DIR_PROD` | `s3://mind-the-data-athena-results-<id>/prod/` | transform-daily |
| `DBT_ATHENA_S3_DATA_DIR_PROD` | `s3://mind-the-data-curated-<id>/prod/` | transform-daily |
| `DBT_ATHENA_WORKGROUP_PROD` | `mind-the-data-prod` | transform-daily |
| `DBT_ATHENA_SCHEMA_PROD` | `mind_the_data_prod` | transform-daily |

A variable that is set wins over the derived value, so the explicit variables still work. Set one only when a name really differs, for example after renaming a bucket or changing the `ci_name_prefix` or `resource_prefix` defaults in Terraform. `ALERT_EMAIL` is separate and optional (see "Alert email").

The `DBT_ATHENA_*_PROD` variables were copied by hand before `AWS_ACCOUNT_ID` existed. To drop them, set `AWS_ACCOUNT_ID` (or run `make bootstrap-apply`), then delete each one whose value equals its derived value. This deletes only exact matches and prints any that differ. Leave the bootstrap-set variables alone: the next `make bootstrap-apply` sets them again anyway.

```sh
id=$(aws sts get-caller-identity --profile mtd-admin --query Account --output text)
gh variable set AWS_ACCOUNT_ID --body "$id"
while read -r name derived; do
  current=$(gh variable get "$name" 2>/dev/null || true)
  if [ "$current" = "$derived" ]; then
    gh variable delete "$name" && echo "deleted $name"
  elif [ -n "$current" ]; then
    echo "kept $name: '$current' differs from '$derived'"
  fi
done <<EOF
DBT_ATHENA_S3_STAGING_DIR_PROD s3://mind-the-data-athena-results-$id/prod/
DBT_ATHENA_S3_DATA_DIR_PROD s3://mind-the-data-curated-$id/prod/
DBT_ATHENA_WORKGROUP_PROD mind-the-data-prod
DBT_ATHENA_SCHEMA_PROD mind_the_data_prod
EOF
```

Locally, `make plan` and `make dbt-build-dev` derive `TF_STATE_BUCKET` and the `DBT_ATHENA_*_DEV` values the same way when `AWS_ACCOUNT_ID` is set (env.example).

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
aws sso login --sso-session mtd   # browser sign-in with MFA
eval "$(aws configure export-credentials --profile mtd-admin --format env)"
terraform -chdir=infra/envs/prod init -backend-config="bucket=$TF_STATE_BUCKET"
terraform -chdir=infra/envs/prod plan -out=prod.tfplan
terraform -chdir=infra/envs/prod apply prod.tfplan
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION
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

**Before plan and apply**, both jobs build the Lambda package and import every handler Terraform names (`make handlers`) from it in a clean interpreter. A pull request that points a function or schedule at code that is not on its branch fails here, so the order pull requests merge in cannot put a schedule live before its code.

**After apply**, the deploy runs a smoke test (`.github/scripts/smoke-test.sh`):

- It invokes the poller once, synchronously, with the event its schedule sends (`{"scheduled_time": "<this minute>"}`), and fails the deploy if the function errors, writes nothing, or its object is not in the raw bucket.
- If raw has no `raw/ref_*` objects yet, it invokes the reference function once the same way.
- If the invoke is throttled (the poller busy with its scheduled run), it retries once a minute later and fails if throttled again.
- Only if the deploy role is not allowed `lambda:InvokeFunction` does it print a warning and pass. Today the apply role has it (through `lambda:*` on `mind-the-data-*`); the escape keeps deploys green if the bootstrap narrows that before granting invoke explicitly. Any other error fails the deploy. There is no rollback: a failed smoke test means the change is live and broken, so fix forward or revert the pull request.

The smoke poll is an extra poll, never a replacement: its objects carry their own `poll_id` in the key and are written with `IfNoneMatch`, so nothing scheduled is overwritten, and the CLI makes one attempt only, so a slow invoke is never sent twice. It starts at second 20 to 40 of a minute, after that minute's scheduled poll has normally finished (they average about 3 s). Each deploy therefore leaves one minute with two polls about 20 to 40 s apart, the same as an overlap without reserved concurrency (ADR-006). Models built on raw must treat polls per minute as one or more, not exactly one.

If `main` moved after the pull request's plan ran, the deploy includes those changes too. Merging soon after the pull request's plan keeps the two the same.

**Deletes and replacements are blocked by default.**

- The pull request's plan flags them with a warning.
- The deploy fails if its plan deletes or replaces any resource, before anything is changed.
- To allow it, put `[allow-destroy]` in the pull request title before merging. The title is always part of the squash commit message.
- Or re-run the `infra` workflow by hand on `main` with `allow_destroy` ticked.

---

## H11: Athena and Glue access for the dev identity

`make dbt-build-dev` and `scripts/athena_query.py` run as the `mtd-dev` identity. Its `ReadOnlyAccess` permission set can't run Athena queries or write dbt's dev tables, so it gets one inline policy: [`docs/policies/mtd-dev-athena.json`](policies/mtd-dev-athena.json). The policy reaches the dev target only:

- the `mind-the-data-dev` workgroup
- read on the `mind_the_data_raw` tables, read and write on the `mind_the_data_dev` database
- read on `raw/` in the raw bucket
- read and write on `dev/` in the curated and Athena results buckets
- **an explicit deny on reading parameters.**
  - It covers `/mind-the-data` and everything under it, so the TfL key stays out of reach even though `ReadOnlyAccess` allows `ssm:Get*`.
  - It also denies `ssm:GetParametersByPath` everywhere. A path read authorises against the path it names, so `--path / --recursive` would otherwise return the key.

Do this after the M2 pull request is merged and deployed, so the workgroup and buckets exist.

**1. Fill in the account id.** The file uses `ACCOUNT_ID` as a placeholder, so no account id is committed.

```sh
ACCOUNT_ID=$(aws sts get-caller-identity --profile mtd-admin --query Account --output text)
sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" docs/policies/mtd-dev-athena.json > /tmp/mtd-dev-athena.json
```

**2. Attach it to the permission set.** In the console, as `mtd-admin`:

1. Go to IAM Identity Center → Multi-account permissions → Permission sets.
2. Open the permission set assigned to the dev user (the one with `ReadOnlyAccess`).
3. Permissions tab → Inline policy → Edit (or "Create inline policy").
4. Switch to the JSON editor, replace its contents with `/tmp/mtd-dev-athena.json`, and save.
5. A banner offers to update the provisioned accounts. Accept it: Identity Center → AWS accounts → this account → "Update permission set" if the banner is gone. Wait until the status is "Provisioned".

**3. Clean up and test.**

`ReadOnlyAccess` can already describe the workgroup, so the test runs a real query. That exercises the workgroup, the results write and the Glue lookups together:

```sh
rm /tmp/mtd-dev-athena.json
aws sso login --sso-session mtd
QID=$(aws athena start-query-execution --profile mtd-dev --region eu-west-2 \
  --work-group mind-the-data-dev \
  --query-string "select count(*) from mind_the_data_raw.line_status where dt = cast(current_date as varchar)" \
  --query QueryExecutionId --output text)
sleep 5
aws athena get-query-execution --profile mtd-dev --region eu-west-2 --query-execution-id "$QID" \
  --query 'QueryExecution.Status.{state:State,reason:StateChangeReason}'
```

Expect `SUCCEEDED`. `AccessDenied` in the reason means the inline policy is missing or not yet provisioned.

The permission set gets this policy for everyone assigned to it. If anyone or anything else uses the same `ReadOnlyAccess` permission set, create a dedicated one for the dev user instead.

Then fill in the dev values in `.env` (next section) and run `make dbt-build-dev`.

---

## H12: Cloudflare R2 and the Pages deploy hook

What `publish-weekly.yml` needs before it publishes. Background and the reasons for each setting are in `docs/site-integration.md` (sections 2 and 9). All in the Cloudflare dashboard, signed in to the account that holds the `karshvashi.com` zone and the `karsh-vashi-website` Pages project.

**Before you start:** the real run in step 7 is the first public publish, and step 9 makes it weekly. Answer D36 (publish while D16 to D23 are open) before either.

1. **Bucket.** R2 Object Storage → Create bucket → name `mind-the-data-public`, location hint Western Europe (WEUR), default storage class Standard. Only published files go in it.
2. **Custom domain.** The bucket → Settings → Custom Domains → Add → `data.karshvashi.com` → Connect. Cloudflare adds the DNS record. Leave the public `r2.dev` URL disabled: it is rate-limited and meant for development.
3. **CORS.** The bucket → Settings → CORS policy → Add, and paste:

   ```json
   [
     {
       "AllowedOrigins": ["*"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": [],
       "ExposeHeaders": ["ETag", "Content-Type"],
       "MaxAgeSeconds": 86400
     }
   ]
   ```

   The site itself fetches at build time and needs no CORS. This rule is for anyone reading the files from a browser.
4. **API token.** R2 Object Storage → Manage API tokens → Create Account API token → name `mind-the-data publish`, permission **Object Read & Write**, "Apply to specific buckets only" → `mind-the-data-public`, no expiry or a dated one you will rotate. Keep the page open: it shows the access key id and secret once. The account id is on the R2 overview page (32 hex characters).
5. **Deploy hook.** Workers & Pages → `karsh-vashi-website` → Settings → Builds → Deploy hooks → Add → name `mind-the-data weekly`, branch `main` → Save, and copy the URL. Anyone holding it can start builds, so treat it as a secret.
6. **GitHub secrets** on this repository (`-R` keeps them off whichever repo the current folder belongs to). Each command prompts for the value, so nothing lands in shell history:

   ```sh
   gh secret set R2_ACCOUNT_ID -R KarshVashi/mind-the-data
   gh secret set R2_ACCESS_KEY_ID -R KarshVashi/mind-the-data
   gh secret set R2_SECRET_ACCESS_KEY -R KarshVashi/mind-the-data
   gh secret set R2_BUCKET -R KarshVashi/mind-the-data               # mind-the-data-public
   gh secret set PAGES_DEPLOY_HOOK_URL -R KarshVashi/mind-the-data
   gh secret list -R KarshVashi/mind-the-data                        # all five listed
   ```

7. **Dry run, then a real run,** both on `main`. Run these from the mind-the-data checkout: `gh` picks the repository from the current folder, and from another repo it answers `HTTP 404: workflow publish-weekly.yml not found`. `-R` makes that explicit:

   ```sh
   gh workflow run publish-weekly.yml -R KarshVashi/mind-the-data --ref main -f dry_run=true
   sleep 10   # let the run register before looking it up
   run=$(gh run list -R KarshVashi/mind-the-data --workflow publish-weekly.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
   gh run watch "$run" -R KarshVashi/mind-the-data --exit-status
   gh run download "$run" -R KarshVashi/mind-the-data --name "publish-$run" --dir ~/mtd-publish
   # check the JSON and the cards in ~/mtd-publish/, then the real run:
   gh workflow run publish-weekly.yml -R KarshVashi/mind-the-data --ref main -f dry_run=false
   ```

   The real run uploads and calls the deploy hook, so it is the first public publish.
8. **Check** the bucket and the headers after the real run:

   ```sh
   curl -sI https://data.karshvashi.com/meta/index.json | grep -iE '^(HTTP|cache-control|content-type)'
   curl -sI -H 'Origin: https://example.com' https://data.karshvashi.com/league/latest.json | grep -i access-control-allow-origin
   ```

   Expect `200`, `cache-control: public, max-age=300`, `content-type: application/json; charset=utf-8` and `access-control-allow-origin: *`. Then check that a new production deployment of the site started (Workers & Pages → `karsh-vashi-website` → Deployments).

9. **Switch on the Monday schedule.** Scheduled runs publish only when this repository variable is `true`, so the secrets can be set and checked before anything goes out on its own (D36):

   ```sh
   gh variable set PUBLISH_ENABLED -R KarshVashi/mind-the-data --body true    # off again: gh variable delete PUBLISH_ENABLED -R KarshVashi/mind-the-data
   ```

**Rotating the token.** Create a new token as in step 4, `gh secret set R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` with its values, run step 7's dry run and a real run, then delete the old token. A leaked deploy hook URL: delete the hook, add a new one, and `gh secret set PAGES_DEPLOY_HOOK_URL`.

---

## dbt connection settings

Terraform outputs every dbt connection setting as `dbt_athena_env`. Reading outputs needs the state, so initialise first (read-only, as in "Planning locally"):

```sh
export AWS_PROFILE=mtd-dev
terraform -chdir=infra/envs/prod init -input=false -backend-config="bucket=$TF_STATE_BUCKET"
terraform -chdir=infra/envs/prod output -json dbt_athena_env
```

| Variable | Dev (`.env`) | Prod (repository variable) |
| --- | --- | --- |
| `DBT_ATHENA_S3_STAGING_DIR_*` | `s3://mind-the-data-athena-results-<account>/dev/` | `s3://mind-the-data-athena-results-<account>/prod/` |
| `DBT_ATHENA_S3_DATA_DIR_*` | `s3://mind-the-data-curated-<account>/dev/` | `s3://mind-the-data-curated-<account>/prod/` |
| `DBT_ATHENA_WORKGROUP_*` | `mind-the-data-dev` | `mind-the-data-prod` |
| `DBT_ATHENA_SCHEMA_*` | `mind_the_data_dev` | `mind_the_data_prod` |

The workgroups enforce their own results location and a 10 GiB scan limit per query, so a client setting cannot override either. Query results expire after 7 days.

The prod values are derived from `AWS_ACCOUNT_ID` in CI (see "Repository variables"), so nothing needs setting. To override one, set it as a repository variable. To check the derived values still match the stack:

```sh
terraform -chdir=infra/envs/prod output -json dbt_athena_env \
  | jq -r 'to_entries[] | select(.key | endswith("_PROD")) | "\(.key) \(.value)"'
```

---

## Daily transform

`.github/workflows/transform-daily.yml` runs `dbt build --target prod` at 03:30 London time every day, as the `mtd-ci-transform` role. It needs `AWS_ACCOUNT_ID` (H6 step 5), from which the role ARN and the dbt settings are derived; without it, or an explicit `AWS_TRANSFORM_ROLE_ARN`, it skips with a notice.

**Why two cron lines.** GitHub's cron runs in UTC only. 03:30 London is 02:30 UTC in summer (BST) and 03:30 UTC in winter (GMT), so the workflow is scheduled at both. Its first step ends the run early unless the slot that fired matches London's current UTC offset: the 02:30 UTC slot builds only while London is on BST, the 03:30 UTC slot only while it is on GMT. Exactly one of the two builds each day. The check uses the slot that fired, not the time the job starts, so a run GitHub starts late under load still builds. The clocks change at 01:00 UTC, before either slot.

**Re-running by hand.** Actions → transform-daily → Run workflow on `main`. A manual run skips the clock check. Its inputs: `select` (a dbt selector, e.g. `fct_line_weekly+`; empty builds everything), `full_refresh` (see "Full refresh in prod") and `maintenance` (run the Iceberg maintenance on a day other than Sunday).

**Sundays** the build is followed by the Iceberg maintenance (see "Iceberg maintenance").

**When the quality gate fails.** `assert_measured_hours_pass_the_gate` runs at `error` (D44), so the run goes red, but it skips only the measured models (`fct_line_measured_daily`, `fct_line_weekly_measured`). The declared tables, the league, divisions and season are built as usual (ADR-014, D55). The next export withholds the measured numbers of every week the measured tables had not finished (`measured_not_rebuilt`); earlier weeks keep theirs. To see why the gate failed, read the test's rows in the run log, then look at the week's hours in prod (the owner's read-only profile; the dev database is built from the same raw data and can stand in when the dev build is current):

```sh
uv run python scripts/athena_query.py "select line_id, count(*) as judged, count_if(not is_in_band) as out_of_band from mind_the_data_prod.int_measured_hours where role = 'core' and is_gate_judged and london_date >= current_date - interval '7' day group by line_id order by 3 desc"
```

Once it passes again, the next daily run rebuilds the measured tables and the following export restores the numbers. Nothing needs to be re-run by hand.

**Industrial action seed.** `assert_announced_industrial_action_is_current` warns, and the daily ops report (`ops-daily`, section "Industrial action seed") opens or updates the `ops-health` issue, when an `announced` row in `transform/seeds/seed_industrial_action.csv` is more than three days past its last day. It never fails the build. To clear it, set the row's `status` to `held` or `called_off` from a public source (update `source_*` to that source), open a pull request, and after merge re-run `transform-daily` so the strikes-excluded league uses it. Until then the strikes-excluded league keeps dropping that action's days (D60).

---

## Stale tmp tables

A dbt run that fails or is cancelled part way can leave `<model>__dbt_tmp` tables in `mind_the_data_prod`. The next build would then stop with `TABLE_ALREADY_EXISTS` on that model. `transform-daily` drops them before every build (`dbt run-operation drop_stale_tmp_relations`), so normally nothing is needed.

If the run shows the "stale tmp cleanup failed" warning, see what is left, read-only:

```sh
cd transform
uv run dbt run-operation drop_stale_tmp_relations --target prod --profiles-dir . --args '{dry_run: true}'
```

Then rerun `transform-daily`, or drop the listed tables as `mtd-admin` with `aws glue delete-table` and remove their S3 location.

---

## Weekly publish

`.github/workflows/publish-weekly.yml` publishes the league every Monday at 06:30 London time, three hours after `transform-daily` has built Sunday. It runs as `mtd-ci-publish` (read-only on the prod marts, queries through the `mind-the-data-prod` workgroup) and:

1. exports the ISO week that ended on Sunday with `python -m mind_the_data.publish.export --publish-workgroup --week <that week>` (every JSON file validated against its schema, plus the two share cards rendered in Chromium);
2. keeps the export as the workflow artifact `publish-<run id>` for 30 days;
3. uploads to R2 with `python -m mind_the_data.publish.upload`: content types and `Cache-Control` per file as in `docs/site-integration.md`, `league/latest.json` and `meta/index.json` last;
4. calls the Pages deploy hook, only after every upload succeeded.

The two cron lines and the clock check work as in the daily transform: 05:30 UTC runs only on BST, 06:30 UTC only on GMT. The clocks change on Sundays.

**Manual runs.** Actions → publish-weekly → Run workflow on `main`. `dry_run` is on by default: the run exports and keeps the artifact, and sends nothing to R2. `week` (for example `2026-W46`) picks a week other than the latest complete one. The publish role trusts only this file on `main` (ADR-010), so a run from any other branch stops with an error.

**When it skips.** Without `AWS_PUBLISH_ROLE_ARN` or `AWS_ACCOUNT_ID` it skips with a notice. Without the five R2 and Pages secrets (H12) a scheduled or non-dry run skips with a notice naming the missing ones; a dry run still exports. A scheduled run also skips with a notice until the `PUBLISH_ENABLED` repository variable is `true` (H12 step 9, D36).

**When it fails.** The site keeps serving last week's build. A failed export uploads nothing: a schema error, a card that overflows, or a week the data does not yet reach past, which on a schedule means Monday's `transform-daily` has not built Sunday (re-run it, then this). The scheduled run never falls back to an older week. A failed upload stops at the failing file, before `meta/index.json` at the latest, so the index never lists a file that is not there; re-run the workflow, as every upload overwrites. A failed hook call after a good upload leaves the files in R2: re-run, or start a build from the Pages dashboard.

**Locally,** with the dev identity: `uv run python -m mind_the_data.publish.export --out-dir build/publish` then `uv run python -m mind_the_data.publish.upload --dir build/publish --dry-run` lists what would be sent. A real upload from a laptop is not part of the process.

---

## Monthly open data

The same workflow file publishes the open dataset (PLAN 8, M7): on the 2nd of each month at 06:45 London time it exports the London month that just ended to `open-data/{yyyy-mm}/` in R2. It runs as `mtd-ci-publish` and:

1. checks the minute model reaches the month's last minute, `fct_line_daily` its last date, and `fct_line_weekly` every week ending in it (otherwise it fails, rather than publishing part of a month);
2. runs Athena `UNLOAD` for each dataset (`line_minute_status`, `line_daily`, `line_weekly`) into a fresh prefix under the prod workgroup's results location, `s3://mind-the-data-athena-results-<account>/prod/open-data/<month>/<run id>/`, the publish role's only AWS write; the results bucket expires them after 7 days;
3. downloads the Parquet parts, checks each is Parquet, names them `<dataset>.parquet` (or `<dataset>-000.parquet` onwards when Athena splits one), and writes `README.md` (columns, licence, "Powered by TfL Open Data") and `manifest.json` (rows, bytes and sha256 per file, validated against `contract/open_data_manifest.schema.json`);
4. keeps it all as the artifact `open-data-<run id>`, and uploads it with `python -m mind_the_data.publish.upload --no-hook`, `manifest.json` last. The site does not read the open data, so the deploy hook is not called.

The measured view is not in it while it is unpublished (D42). The schedule needs H12 and the `OPEN_DATA_ENABLED` repository variable, which waits for H12 (the licence, CC BY 4.0, is settled: D63):

```sh
gh variable set OPEN_DATA_ENABLED --body true    # off again: gh variable delete OPEN_DATA_ENABLED
```

**Manual runs.** Actions → publish-weekly → Run workflow on `main` with `export: open-data`; `month` (e.g. `2026-11`) defaults to last month, and `dry_run` is on by default:

```sh
gh workflow run publish-weekly.yml --ref main -f export=open-data -f month=2026-11 -f dry_run=true
```

Any run that uploads open data, manual or scheduled, needs `OPEN_DATA_ENABLED`, because the README states a licence (D63); a dry run does not. The job joins the `transform-prod` concurrency group, so it never reads the marts while a transform rebuilds them.

Re-running a month overwrites its files. Athena can split a dataset into a different number of parts from one run to the next, and the upload never deletes, so it refuses a re-export that would leave an old part of the month in place and names the keys. Delete those keys in the Cloudflare dashboard (R2 → `mind-the-data-public` → `open-data/<month>/`), then re-run.

**Locally:** `uv run python -m mind_the_data.publish.open_data --month 2026-11 --dry-run` prints every statement and needs no AWS access.

---

## Alarms: what to do

Every alarm emails the `mind-the-data-alerts` topic ("Alert email"). The table in "Collector alarms" says when each fires; this is what to do. Start with `make status`, which shows alarm state, raw freshness per feed and the last workflow runs.

| Alarm | Do this |
| --- | --- |
| `mind-the-data-poller-error-rate` | Read the last errors: `aws logs tail /aws/lambda/mind-the-data-poller --profile mtd-dev --region eu-west-2 --since 30m --filter-pattern ERROR`. A TfL outage clears on its own; 401 or 403 is the key (see "Rotating the TfL key"); a parse error means TfL changed a payload, and raw still holds every response for replay once the model is fixed. |
| `mind-the-data-poller-throttles` | Check the reserved concurrency and the account limit ("Lambda concurrency quota"). A poll running past a minute shows as a duration near the 45 s budget in the logs. |
| `mind-the-data-no-objects-<feed>` | Is the schedule enabled? `aws scheduler get-schedule --profile mtd-dev --region eu-west-2 --name mind-the-data-poller --query State`. Then the poller logs. If only `arrivals` stopped, check `config/stations.yaml` against the last deploy. If only `line_arrivals` stopped, check the `line_arrivals` write errors in the poller logs; the other feeds keep being written. |
| `mind-the-data-tfl-failures-line-status` | Every line-status call fails: the key first, then TfL's own status page. |
| `mind-the-data-reference-errors` | The daily reference fetch failed. A missed day leaves that day's sixth of the network (or, on Mondays, the measurement stations) on last week's timetables until the same weekday next week. Re-run it for the same day with the invoke command in "Collector alarms". |

**Gaps.** Data the poller missed cannot be fetched again: TfL's API has no history. The minutes stay `is_missing` and lower that week's completeness, which the league shows; they are never read as Good Service. Note the cause in the week's data-quality page if it is more than a few minutes.

A failing **transform-daily** run (an email from GitHub, not an alarm) leaves yesterday's marts in place. Read the failed test in the run log, fix forward on a branch, then re-run (below).

---

## Re-running

Every workflow can be re-run on `main` from Actions → the workflow → Run workflow, or with `gh`:

```sh
gh workflow run transform-daily.yml --ref main                       # rebuild prod now
gh workflow run publish-weekly.yml --ref main -f dry_run=true        # export, keep as artifact
gh workflow run publish-weekly.yml --ref main -f dry_run=false -f week=2026-W46
gh workflow run ops-daily.yml --ref main -f day=2026-11-13
gh run list --workflow transform-daily.yml --limit 3                 # then: gh run watch <id>
```

A re-run is always safe: staging merges whole days on a row key (ADR-008), the other models rebuild in full, and every upload overwrites. "Re-run failed jobs" on a finished run reuses its inputs and the commit it ran at.

---

## Backfilling the marts from raw

Raw is kept forever and everything downstream rebuilds from it.

- **Missed daily runs** (up to `lookback_days`, 2): the next run picks the days up. Nothing to do.
- **Longer outage of the transform**, or a raw object that arrived late for an older day: rerun staging over the days, then everything downstream. Staging reads only the window, the rest rebuilds in full:

  ```sh
  cd transform
  uv run --locked dbt build --target dev --profiles-dir . --select staging+ \
    --vars '{start_date: "2026-11-03", end_date: "2026-11-05"}'
  ```

  That is the dev target, as the dev identity. In prod the same window needs the transform role: run `transform-daily` with `select` set to `staging+`. The workflow does not take `start_date`/`end_date`, so for a window older than the lookback use a full refresh of the affected staging models (below), which reads from `raw_start_date`.
- **A metric definition changed** (a seed or an intermediate model): the next daily run rebuilds every intermediate and mart model in full, so all history follows (ADR-012). Nothing to do.

---

## Full refresh in prod

A staging model whose logic drops rows or whose merge key changes (D8, D45) has to be rebuilt from raw. In prod this runs in CI as `mtd-ci-transform`, not from a laptop as admin:

```sh
gh workflow run transform-daily.yml --ref main -f full_refresh=true -f select='stg_arrivals+'
run=$(gh run list --workflow transform-daily.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$run" --exit-status
```

The guards:

- the inputs exist only on a manual run, so no schedule can full-refresh;
- `full_refresh` needs a selector; `path:models` rebuilds everything, which reads all of raw since `raw_start_date` and can take most of the job's 55 minutes;
- the selector may hold only model names, paths and dbt selector characters, and reaches dbt as separate arguments;
- the run takes the `transform-prod` concurrency group, so it never overlaps the daily build.

Add `+` after a staging model so its dependants rebuild in the same run; otherwise they keep the old rows until the next daily build. The run's log starts with the exact dbt command.

---

## Iceberg maintenance

On Sundays (the London date) `transform-daily` runs `dbt run-operation iceberg_maintenance --target prod` after the build, as the transform role. For each incremental Iceberg model (the staging tables, D64) it runs:

- `optimize <table> rewrite data using bin_pack where dt >= date '<9 days ago>'`: compacts the small files the daily merges leave, in the partitions they touched since last Sunday (`maintenance_lookback_days`);
- `vacuum <table>`: expires snapshots older than the table's `vacuum_max_snapshot_age_seconds` (Athena's default, 5 days) and deletes files no snapshot uses.

Tables rebuilt in full each run start fresh every day, so they are not touched. Both statements are retried while Athena answers `ICEBERG_*_MORE_RUNS_NEEDED`. The step also runs when the build failed (not when it was cancelled), and the job still ends failed.

After a VACUUM, Iceberg time travel on staging reaches back about 5 days.

**By hand:**

```sh
gh workflow run transform-daily.yml --ref main -f maintenance=true
# Locally, print the statements without running them (no AWS calls):
cd transform && uv run --locked dbt run-operation iceberg_maintenance --target dev --profiles-dir . --args '{dry_run: true}'
```

`--args '{full: true}'` compacts every partition, for the first run or after a large backfill. Each statement is under the workgroup's 10 GiB scan cap; if one ever hits it, run with a smaller `maintenance_lookback_days`.

---

## Rotating the TfL key

The key lives only in SSM at `/mind-the-data/tfl/app_key`. Never print it, paste it into a chat or a file, or read it back with `get-parameter --with-decryption`.

1. In the TfL API portal, add a second key or regenerate the secondary key (keep the old one working for now).
2. Overwrite the parameter as the admin identity. `read -s` keeps the key off the screen and out of shell history:

   ```sh
   read -rs TFL_APP_KEY && \
   aws ssm put-parameter --profile mtd-admin --region eu-west-2 \
     --name /mind-the-data/tfl/app_key --type SecureString \
     --value "$TFL_APP_KEY" --overwrite ; \
   unset TFL_APP_KEY
   ```

3. Check the version went up, without the value:

   ```sh
   aws ssm describe-parameters --profile mtd-dev --region eu-west-2 \
     --parameter-filters Key=Name,Values=/mind-the-data/tfl/app_key \
     --query 'Parameters[].{version:Version,modified:LastModifiedDate}'
   ```

4. The poller reads the key at cold start, and again after any 401 or 403, so new invocations use it within minutes with no deploy. Confirm with `make status` (raw age per feed under two minutes) and no `tfl-failures` alarm for 15 minutes.
5. Revoke the old key in the portal.

If the key leaked: revoke it in the portal first, then steps 2 to 4. The `tfl-failures` alarm fires in between; that is expected.

---

## Alert email

Alarms notify the `mind-the-data-alerts` SNS topic. The email address comes from the `ALERT_EMAIL` repository variable. If it is unset, the topic has no subscription and alarms notify nobody, so set it before the collector's first deploy:

```sh
gh variable set ALERT_EMAIL --body "you@example.com"
```

After the next deploy, AWS sends a confirmation email to that address. Click the link in it. Until you do, the subscription stays pending and no alarm email arrives.

Check the subscription:

```sh
aws sns list-subscriptions-by-topic --profile mtd-dev --region eu-west-2 \
  --topic-arn "arn:aws:sns:eu-west-2:$(aws sts get-caller-identity --profile mtd-dev --query Account --output text):mind-the-data-alerts" \
  --query 'Subscriptions[].{endpoint:Endpoint,arn:SubscriptionArn}'
```

`PendingConfirmation` in place of an ARN means the link has not been clicked.

---

## Lambda concurrency quota

The account started with a Lambda concurrency quota of 10, too low for Lambda to reserve concurrency for any function (ADR-006). A request for 1,000 made on 2026-11-06 was approved on 2026-11-07.

The bootstrap now holds the quota (`aws_servicequotas_service_quota.lambda_concurrency`, see H6). If the value ever drops below 1,000, a bootstrap apply asks for the increase. If a request is already open, the apply logs a warning and sends nothing. Don't also request it by hand.

Check requests:

```sh
aws service-quotas list-requested-service-quota-change-history-by-quota --profile mtd-dev --region eu-west-2 \
  --service-code lambda --quota-code L-B99A9384 --query 'RequestedQuotas[].[Status,DesiredValue,Created]'
```

Check the limit itself:

```sh
aws lambda get-account-settings --profile mtd-dev --region eu-west-2 --query AccountLimit.ConcurrentExecutions
```

It has returned 1000 since 2026-11-07, and `poller_reserved_concurrency` in `infra/envs/prod/variables.tf` now defaults to `1` (ADR-006). If the quota ever falls back to 10, set it to `-1` before the next deploy, or `PutFunctionConcurrency` fails.

---

## Collector alarms

| Alarm | Fires when | First check |
| --- | --- | --- |
| `mind-the-data-poller-error-rate` | The poller ends in an unhandled error in 3 of the last 5 minutes | Poller logs (`/aws/lambda/mind-the-data-poller`) |
| `mind-the-data-poller-throttles` | Any poller invocation is throttled in 5 minutes | Account concurrency (above); a poll running past a minute |
| `mind-the-data-no-objects-line-status` | No `line_status` object is written for 10 minutes, including when the poller isn't running at all | Schedule state, then poller logs |
| `mind-the-data-no-objects-line-disruption` | No `line_disruption` object is written for 10 minutes | Poller logs |
| `mind-the-data-no-objects-arrivals` | No `arrivals` object is written for 10 minutes | Poller logs; station config |
| `mind-the-data-no-objects-line-arrivals` | No `line_arrivals` object (one `Line/{id}/Arrivals` call per configured line, ADR-016) is written for 10 minutes | Poller logs |
| `mind-the-data-tfl-failures-line-status` | Every line status request to TfL fails for 5 minutes | The app key (H8), then TfL's status |
| `mind-the-data-reference-errors` | The daily reference fetch ends in an error | Reference logs (`/aws/lambda/mind-the-data-reference`) |

A request that TfL rejects is still written to raw, as a failure envelope (ADR-003). When every request for a feed fails, which is what a revoked or disabled key looks like, the poller writes those envelopes and then ends the invocation in an error.

**What a revoked or disabled key sets off:**

- **`mind-the-data-poller-error-rate`**, within about 3 minutes.
- **`mind-the-data-tfl-failures-line-status`**, after 5 minutes.
- **Not the no-objects alarms.** Objects are still written.

A 401 or 403 also makes the next run read the key from SSM again, so a rotated key is picked up without a redeploy.

The same applies to `line_arrivals` alone: if every line-wide request fails, its failure envelopes and every other feed are still written, and the invocation then ends in an error, so the error-rate alarm fires.

**Collection volumes (M8, ADR-016).** Each poll makes one Arrivals call per measurement station (50), one `Line/{id}/Arrivals` call per configured line (20), line status and line disruption: 72 calls a minute, 73 every fifth minute with stop disruption. That is about 15% of the 500 a minute key limit, at concurrency 10 inside the 45-second budget. `config/` is rejected at load if a poll would exceed 80 calls. The line-wide calls are queued last, so if TfL is slow they run out of budget first (`budget_exhausted` envelopes) and the station boards are unaffected.

**Reference fetch cadence.** The reference Lambda runs daily at 03:00 London time (`mind-the-data-reference-daily`). Every run fetches severity codes and 40 route sequences. Monday's run fetches the measurement stations' timetables (156 calls, as when the job was weekly). Tuesday to Sunday each fetch about 110 to 145 timetables: one sixth of every other station on the tube, DLR and tram lines, taken from that run's route sequences, both directions. A station's day comes from a CRC-32 of `line/naptan`, so it is the same every week. Elizabeth line and Overground stations other than the measurement ones are skipped: TfL returns 404 for all their timetables. Runs are 153 to 197 calls at concurrency 4, within a 540-second budget (600-second timeout); at TfL's usual 03:00 timetable latency a 197-call run took about 240 seconds.

**After the collector's first deploy**, run the reference fetch once rather than waiting for 03:00:

```sh
aws lambda invoke --profile mtd-admin --region eu-west-2 \
  --function-name mind-the-data-reference --invocation-type Event /dev/null
```

To re-run a given day's share (for example after a failed run), pass that day's scheduled time:

```sh
aws lambda invoke --profile mtd-admin --region eu-west-2 \
  --function-name mind-the-data-reference --invocation-type Event \
  --cli-binary-format raw-in-base64-out \
  --payload '{"scheduled_time": "2026-11-17T03:00:00Z"}' /dev/null
```

Locally, `uv run python -m mind_the_data.ingest.reference --local --out ./tmp --date 2026-11-17` fetches the same share into `./tmp`.

---

## Planning locally

```sh
export AWS_PROFILE=mtd-dev   # read-only
export TF_STATE_BUCKET=<state_bucket output>
make plan
```

`make plan` runs without a state lock, because the dev profile is read-only.

## Rebuilding staging days

Staging models merge whole UTC days on a row key (ADR-008), so rerunning any range of days is safe and gives the same rows.

```sh
cd transform
# Default daily run: reprocesses the last 2 UTC days (var lookback_days).
uv run --locked dbt build --target dev --profiles-dir . --select staging
# Backfill or repair specific days (inclusive, UTC):
uv run --locked dbt build --target dev --profiles-dir . --select staging \
  --vars '{start_date: "2026-11-03", end_date: "2026-11-05"}'
# After changing a staging model's logic, rebuild it from raw_start_date:
uv run --locked dbt build --target dev --profiles-dir . --select stg_arrivals --full-refresh
```

A merge never deletes rows, so a logic change that drops rows needs `--full-refresh`. Never combine `--full-refresh` with `start_date`/`end_date`: the table would keep only that window, so dbt refuses to compile it.

A change to a staging model's merge key (D45 for `stg_arrivals`) needs the same full refresh in prod, before the next daily run. Until then the daily run rereads its days under the new key beside the old rows, `assert_staged_arrivals_match_raw` fails, and everything downstream of `stg_arrivals` is skipped, `fct_line_weekly` included: it keeps its last build until the refresh. The prod command is in docs/STATUS.md, "Owner steps outstanding".

---

## Station models

The every-station models (M8, ADR-016) carry the dbt tag `station`: `stg_line_arrivals`, `stg_line_arrival_polls`, `stg_route_station`, `dim_station`, `fct_station_disruption_weekly`, the `int_station_*` models and `fct_station_weekly_measured`. Nothing outside the tag depends on them, so the daily transform builds them in their own step (`--select tag:station`) that may fail without stopping the league build. A failure there leaves the station tables at their last good build; the line-level tables are unaffected.

**Windowed models.** The line feed is about 10 GB of raw a month, and the workgroup stops any query over 10 GiB, so `int_station_inferred_arrivals` and `int_station_vehicle_passes` are never rebuilt in full. Each run deletes the days of its window (the last `lookback_days` UTC days, or `start_date` to `end_date`) and recomputes them, reading one day before the window. A first build or a `--full-refresh` without `start_date` covers only the last `station_initial_days` days (default 7), and so do `stg_line_arrivals` and `stg_line_arrival_polls`.

**Backfilling.** Load older days in chunks of a few days, oldest first, after the first build has created the tables:

```sh
cd transform
uv run --locked dbt build --target prod --profiles-dir . --select tag:station \
  --vars '{start_date: "2026-11-18", end_date: "2026-11-21"}'
```

The staging models merge those days, the two windowed models replace them, and the tables downstream rebuild in full from what is there. Keep each chunk's line feed under the 10 GiB scan limit (a few days at the current volume).

**After a failed run.** The windowed models delete their window's days in a pre-hook before recomputing them. If the run fails after the delete, those days are missing from `int_station_inferred_arrivals` or `int_station_vehicle_passes` (and so from the station views) until the next green run recomputes them. Nothing is lost: raw and `stg_line_arrivals` still hold every poll. If the failure lasted longer than `lookback_days`, rebuild the missed days with `start_date`/`end_date` as above.

---

## Ops workflows

Scheduled checks that answer "is the collector healthy?" without anyone asking. Both run read-only as the `mtd-ci-observer` role, created by the bootstrap. The role trusts these two workflow files by name, so don't rename them.

| Workflow | When | What it checks | Issue label |
| --- | --- | --- | --- |
| `ops-daily` | Every day at 02:10 UTC | The previous UTC day: completeness (`scripts/completeness.py`), alarm state and any alarm that fired, poller and reference Lambda errors and throttles, the newest raw object per feed, and announced strikes left in the industrial action seed past their date | `ops-health` |
| `ops-daily` (job `published`) | Every day at 02:10 UTC | The published output on R2 over HTTPS, no AWS (it does not see the Pages deploy hook): `meta/index.json` and `league/latest.json` `generated_at` no more than 8 days old, `league/latest.json` on the last complete ISO week (the run is at 02:10 UTC, so a missed Monday publish shows on Tuesday), and the `schema_version` major equal to the code's | `ops-publish` |
| `ops-daytime` | Weekdays at 12:00 UTC | The strict arrivals check: every configured line shows predictions at its station in the last 15 minutes of raw arrivals | `ops-daytime` |

Each run writes its report to the run summary. When something misses its target, it opens one issue with that label, or comments on the open one. The first run that meets every target closes it. So an open `ops-health`, `ops-publish` or `ops-daytime` issue means something is wrong now.

The daytime check reads what the collector stored rather than calling TfL. CI never needs the TfL key, and the check tests the data the league table is built from.

**Turning them on.** They run once the `AWS_OBSERVER_ROLE_ARN` or `AWS_ACCOUNT_ID` repository variable is set (see "Repository variables"). `make bootstrap-apply` sets it from the bootstrap outputs (H6, step 3), so after the apply that created `mtd-ci-observer` there is nothing more to do. To check:

```sh
gh variable get AWS_OBSERVER_ROLE_ARN   # arn:aws:iam::<account>:role/mtd-ci-observer
```

Without the variable, both workflows skip with a warning and leave issues alone. When the role can't be assumed they fail (D13), so a broken trust shows red instead of hiding a dead collector. The role trusts only runs on `main`, so a run started from another branch fails too. To try one at once: Actions → ops-daily → Run workflow. `ops-daily` takes an optional `day` (YYYY-MM-DD).

**Re-recording test fixtures.** Run `ops-daytime` by hand during service hours with `record_fixtures` ticked. It calls TfL anonymously, with no key and no AWS credentials in the job, and runs the unit tests against the new recording. The fixtures come back as the `tfl-fixtures` artifact. To use them, copy them over `tests/fixtures/tfl/` on a branch and open a pull request. Nothing is committed automatically.

**Locally.**

```sh
aws sso login --sso-session mtd
make status                      # PRs and checks, last workflow runs, alarms, raw age per feed, open ops issues
make prune-worktrees DRY_RUN=1   # which local worktrees are finished, and the space they hold
make prune-worktrees             # remove them
```

`make status` uses the read-only `mtd-dev` profile (override with `MTD_PROFILE`). Any part it can't reach is shown as unavailable, and the rest still prints.

`make prune-worktrees` looks only inside `WORKTREE_DIR` (default `.worktrees` in the repo root; set it to wherever you keep local worktrees, relative to the repo root or absolute; the repo root itself or a parent of it is refused), and removes a worktree only when all of these hold:

- it has no uncommitted or untracked changes and isn't locked;
- nothing in it changed in the last 24 hours (`IDLE_HOURS` overrides this), so work still in progress there is left alone;
- its work is safe elsewhere: it is already in `origin/main`, or a merged pull request's head is exactly its HEAD.

Ignored files in a removed worktree (`.venv`, `.terraform`, dbt `target/`, local `.env` copies) go with it. Its local branch is deleted. Remote branches are never touched.

**Data transfer.** `ops-daily` reads about 55 MB of raw objects from eu-west-2 a day. That is S3 egress to the internet, about 1.7 GB a month, well inside the 100 GB monthly free allowance.

---

## Snapshot for a post

The numbers and share cards for a window of London dates that is not an ISO week (the first post runs Wednesday to Tuesday). Read-only: it queries the prod marts through the dev Athena workgroup as `mtd-dev`, and writes nothing to AWS.

```sh
AWS_PROFILE=mtd-dev uv run python scripts/post_snapshot.py \
  --start 2026-11-04 --end 2026-11-10 --out-dir ~/mtd-snapshots/2026-11-11
```

It writes `numbers.md` (league, Disruption Index, Good Service %, completeness per line, longest incidents, and how far the data reaches), `league.json` (the league contract) and `card.png` / `card-1080x1350.png`. Check `numbers.md` before posting: the provisional flags and the open decisions are listed there. The cards render with Playwright: run `uv run playwright install chromium` once, or pass `--browser-channel chrome` to use a local Google Chrome. A card whose content does not fit is refused, not written.

The weekly files for an ISO week (`league/`, `lines/`, `meta/`, `cards/`) come from `uv run python -m mind_the_data.publish.export --week 2026-W46 --out-dir build/publish`; `--dry-run` validates them against `src/mind_the_data/publish/contract/` and writes nothing. A week that has not finished, or that the data does not yet reach past, is refused; `--allow-incomplete` writes it flagged `is_window_complete: false` and never as `league/latest.json`. Every run also rewrites the league file of every complete week with data, `league/latest.json` (always the newest complete week) and `meta/index.json`, the list of archived weeks and line files that the website reads first (D39, `docs/site-integration.md`). `git_sha` is the publishing checkout's commit, suffixed `-dirty` when it has uncommitted changes. Uploading is the weekly workflow's job ("Weekly publish").

## Merge gate

Low-risk pull requests can be merged by the project's engineer without the owner, through a gate (ADR-011). Everything else waits for the owner, as before.

```sh
make merge-check PR=<n>   # dry run: prints the verdict and every reason
make merge PR=<n>         # squash-merges the pinned head commit, only on ALLOW
```

**What it allows.** All of these must hold:

- The pull request is open, targets `main`, is not a draft, and its author is in the allowlist (`KarshVashi`).
- Every check on the head commit has finished, and none failed or was cancelled. `ci / ci` must succeed. `infra / plan` must also succeed when anything deployable changed (`infra/`, `src/`, `config/`, the Lambda build inputs, `Makefile`).
- No protected path changed. Protected paths are `infra/bootstrap/**`, `infra/modules/ci_role/**`, `.github/**`, `docs/adr/**`, the gate's own files, `GNUmakefile` and `makefile` (make reads them before `Makefile`), and anything with `budget` or `quota` in its path.
- The Makefile changed, if at all, outside the marked merge gate block, and no changed line could alter how `make merge` runs (a second `merge` rule, `include`, `SHELL`, `PATH`, `export`, `GIT_*` or `UV_*` variables, `$(shell)`, `$(eval)`, conditionals and similar).
- When something deployable changed, the pull request's own CI plan is used, downloaded as the `plan-changes` artifact of the `infra / plan` run for the head commit. It may only create or update resource types on the allowlist in `config/merge_policy.yaml`. A local plan is never used, since it differs from CI's (no `ALERT_EMAIL`, a different zip hash).
- No `[allow-destroy]` in the title, body or any commit message, since that marker lets the deploy delete resources.

**What it refuses.** Anything else, and anything it cannot check. That includes any `aws_iam_*` change, any delete or replace, any import, any resource type not on the list, a plan from a manual `workflow_dispatch` run, a missing or expired plan artifact (it is kept for 7 days, so re-run the plan job), a plan run for a different commit, and pending checks. On a refusal it exits 1 and merges nothing.

**Pinning.** The gate reads the head SHA once, checks everything against it, and merges with `gh pr merge --squash --match-head-commit <sha>`. A push after the check makes GitHub refuse the merge.

**It always runs main's copy.** `make merge` fetches `main` into `refs/remotes/origin/main` without tags, and uses that full ref throughout, so a tag or local branch called `origin/main` cannot stand in. It exports `scripts/merge_gate.py`, `src/mind_the_data/ops/`, `config/merge_policy.yaml`, `pyproject.toml` and `uv.lock` from `origin/main` into a temporary directory with `git archive`, builds an environment from that lock file, and runs the gate there with `python -I`. The gate loads its module by file path, never through the installed package. The rules applied are the ones already on `main`, whatever a pull request changes, so a feature branch cannot loosen its own gate.

The `make` recipe itself comes from the checked-out Makefile. It refuses to run unless its merge gate block matches the copy it exports. That catches running from a branch by accident, but a deliberately edited recipe could skip the check. So run `make merge` from a checkout of `main` (or a branch that does not touch the block).

`make merge-check PR=<n> MERGE_GATE_REF=<ref>` dry-runs a proposed gate from another ref. `make merge` ignores that variable. The proposed code runs with your `gh` login, so read it before you dry-run it.

**Changing the policy.** Edit `config/merge_policy.yaml` (or the gate's code) on a branch and open a pull request. Those files are protected, so the gate refuses that pull request and only the owner can merge it. Try it first with `make merge-check PR=<n> MERGE_GATE_REF=<branch>`. The loader rejects attempts to switch off the fixed rules (`unlisted_types`, `not_draft`, `checks_succeeded`, `pin_head_sha`).

**It is a process guard, not a security boundary.** Anyone with the owner's `gh` login could run `gh pr merge` directly. The gate makes the safe path the easy one and leaves a printed record of why each merge was allowed. The AWS Budget alerts remain the backstop after a merge.
