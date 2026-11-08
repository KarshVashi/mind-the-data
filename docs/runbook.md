# Runbook

Commands for the steps a person has to do. Run them from the repository root unless stated. Region is `eu-west-2` throughout.

The rest of the runbook (alarms, re-runs, backfills, key rotation in depth) arrives with M1 and M7.

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

**5. Bind the roles to their workflows (ADR-010).** Do this once, after the first apply that includes `mtd-ci-observer`.

1. Run the trust probe:

   ```sh
   gh workflow run bootstrap-plan.yml --ref main -f trust_probe=new-roles
   gh run watch "$(gh run list --workflow bootstrap-plan.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```

   All of its jobs must pass:
   - `plan` and `trust-probe` show that the plan role accepts `bootstrap-plan.yml` on `main`, and that the observer and publish roles refuse it.
   - `oidc-claims` writes the claims into the run summary.
2. Check the pull request form. Open the follow-up pull request from step 3 and look at the summary of its `bootstrap-plan / oidc-claims` job. `job_workflow_ref` must read `KarshVashi/mind-the-data/.github/workflows/bootstrap-plan.yml@refs/pull/<number>/merge`, which is the form the plan role will match on pull requests. If it reads differently, stop and fix `plan_pull_request` in `infra/modules/ci_role/main.tf` first.
3. Merge the follow-up change that sets `enforce_workflow_binding` to `true` in `infra/bootstrap/variables.tf`.
4. Run `make bootstrap-apply` again.
5. Run the probe again with `-f trust_probe=all-roles`.
6. Open or update any pull request that touches `infra/`. Its `infra / plan` job must still assume the plan role.

If step 1 or 2 fails, stop. Leave `enforce_workflow_binding` off and read ADR-010's fallback.

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

`AWS_ACCOUNT_ID` is the only variable the workflows need. Everything else follows the naming convention in `infra/`. `make bootstrap-apply` (H6, step 3) sets `AWS_ACCOUNT_ID` and the first four rows below from the bootstrap outputs; the `DBT_ATHENA_*_PROD` values are only derived:

| Variable | Derived from `AWS_ACCOUNT_ID` | Used by |
| --- | --- | --- |
| `TF_STATE_BUCKET` | `mtd-tfstate-<id>` | infra |
| `AWS_PLAN_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-plan` | infra (pull requests) |
| `AWS_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-apply` | infra (main) |
| `AWS_TRANSFORM_ROLE_ARN` | `arn:aws:iam::<id>:role/mtd-ci-transform` | transform-daily |
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

**Re-running by hand.** Actions → transform-daily → Run workflow on `main`. A manual run skips the clock check.

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

Once this returns 1000, open a pull request that changes the default of `poller_reserved_concurrency` in `infra/envs/prod/variables.tf` to `1`.

---

## Collector alarms

| Alarm | Fires when | First check |
| --- | --- | --- |
| `mind-the-data-poller-error-rate` | The poller ends in an unhandled error in 3 of the last 5 minutes | Poller logs (`/aws/lambda/mind-the-data-poller`) |
| `mind-the-data-poller-throttles` | Any poller invocation is throttled in 5 minutes | Account concurrency (above); a poll running past a minute |
| `mind-the-data-no-objects-line-status` | No `line_status` object is written for 10 minutes, including when the poller isn't running at all | Schedule state, then poller logs |
| `mind-the-data-no-objects-line-disruption` | No `line_disruption` object is written for 10 minutes | Poller logs |
| `mind-the-data-no-objects-arrivals` | No `arrivals` object is written for 10 minutes | Poller logs; station config |
| `mind-the-data-tfl-failures-line-status` | Every line status request to TfL fails for 5 minutes | The app key (H8), then TfL's status |
| `mind-the-data-reference-errors` | The weekly reference fetch ends in an error | Reference logs (`/aws/lambda/mind-the-data-reference`) |

A request that TfL rejects is still written to raw, as a failure envelope (ADR-003). When every request for a feed fails, which is what a revoked or disabled key looks like, the poller writes those envelopes and then ends the invocation in an error.

**What a revoked or disabled key sets off:**

- **`mind-the-data-poller-error-rate`**, within about 3 minutes.
- **`mind-the-data-tfl-failures-line-status`**, after 5 minutes.
- **Not the no-objects alarms.** Objects are still written.

A 401 or 403 also makes the next run read the key from SSM again, so a rotated key is picked up without a redeploy.

**After the collector's first deploy**, run the reference fetch once rather than waiting for Monday 03:00:

```sh
aws lambda invoke --profile mtd-admin --region eu-west-2 \
  --function-name mind-the-data-reference --invocation-type Event /dev/null
```

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

---

## Ops workflows

Scheduled checks that answer "is the collector healthy?" without anyone asking. Both run read-only as the `mtd-ci-observer` role, created by the bootstrap. The role trusts these two workflow files by name, so don't rename them.

| Workflow | When | What it checks | Issue label |
| --- | --- | --- | --- |
| `ops-daily` | Every day at 02:10 UTC | The previous UTC day: completeness (`scripts/completeness.py`), alarm state and any alarm that fired, poller and reference Lambda errors and throttles, and the newest raw object per feed | `ops-health` |
| `ops-daytime` | Weekdays at 12:00 UTC | The strict arrivals check: every configured line shows predictions at its station in the last 15 minutes of raw arrivals | `ops-daytime` |

Each run writes its report to the run summary. When something misses its target, it opens one issue with that label, or comments on the open one. The first run that meets every target closes it. So an open `ops-health` or `ops-daytime` issue means something is wrong now.

The daytime check reads what the collector stored rather than calling TfL. CI never needs the TfL key, and the check tests the data the league table is built from.

**Turning them on.** They run once the `AWS_OBSERVER_ROLE_ARN` repository variable is set. `make bootstrap-apply` sets it from the bootstrap outputs (H6, step 3), so after the apply that created `mtd-ci-observer` there is nothing more to do. To check:

```sh
gh variable get AWS_OBSERVER_ROLE_ARN   # arn:aws:iam::<account>:role/mtd-ci-observer
```

Without the variable, and whenever the role can't be assumed, both workflows skip with a warning and leave issues alone. The role trusts only runs on `main`, so a run started from another branch skips too. To try one at once: Actions → ops-daily → Run workflow. `ops-daily` takes an optional `day` (YYYY-MM-DD).

**Re-recording test fixtures.** Run `ops-daytime` by hand during service hours with `record_fixtures` ticked. It calls TfL anonymously, with no key and no AWS credentials in the job, and runs the unit tests against the new recording. The fixtures come back as the `tfl-fixtures` artifact. To use them, copy them over `tests/fixtures/tfl/` on a branch and open a pull request. Nothing is committed automatically.

**Locally.**

```sh
aws sso login --sso-session mtd
make status                      # PRs and checks, last workflow runs, alarms, raw age per feed, open ops issues
make prune-worktrees DRY_RUN=1   # which .claude/worktrees are finished, and the space they hold
make prune-worktrees             # remove them
```

`make status` uses the read-only `mtd-dev` profile (override with `MTD_PROFILE`). Any part it can't reach is shown as unavailable, and the rest still prints.

`make prune-worktrees` removes a worktree only when all of these hold:

- it has no uncommitted or untracked changes and isn't locked;
- nothing in it changed in the last 24 hours (`IDLE_HOURS` overrides this), so a session still working there is left alone;
- its work is safe elsewhere: it is already in `origin/main`, or a merged pull request's head is exactly its HEAD.

Ignored files in a removed worktree (`.venv`, `.terraform`, dbt `target/`, local `.env` copies) go with it. Its local branch is deleted. Remote branches are never touched.

**Data transfer.** `ops-daily` reads about 55 MB of raw objects from eu-west-2 a day. That is S3 egress to the internet, about 1.7 GB a month, well inside the 100 GB monthly free allowance.

### Automerge (off)

`.github/workflows/automerge.yml` squash-merges a pull request after `ci` passes, if all of these hold:

- you opened it, from a branch in this repository, and it isn't a draft;
- nothing was pushed after `ci` ran, and every check passed;
- every changed file is under `transform/models/`, `tests/` or `docs/`, but not `docs/adr/`.

It runs only when the repository variable `AUTOMERGE_ENABLED` is `true`.

Notes:

- GitHub Free doesn't offer auto-merge (`gh pr merge --auto`) on private repositories. ci has already passed when this runs, so it merges directly, pinned to the commit ci checked.
- A merge made with `GITHUB_TOKEN` starts no other workflow, so neither `ci` nor `infra` runs on `main` for it. Nothing in the allowed paths is deployed by `infra`. The daily transform picks up model changes at its next run.

**Keep it off until the CI roles' trust is pinned to `job_workflow_ref`** (`enforce_workflow_binding`, H6 step 5). Until that is applied, the apply and transform roles trust any job running on `main`. Automerge puts changes on `main` without a person reading them first. A dbt model is SQL plus Jinja, and it runs on `main` as the transform role. Once each role trusts only its own workflow file, an unreviewed change can't reach any role except through the workflows already reviewed. Then turn it on:

```sh
gh variable set AUTOMERGE_ENABLED --body true    # off again: gh variable delete AUTOMERGE_ENABLED
```
