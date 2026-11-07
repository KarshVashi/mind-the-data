# Status

Updated: 2026-11-07. Every PR updates this page. Pending decisions live in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done | Polling since 2026-11-04. Poller runs unreserved until the quota rises (ADR-006). |
| M2 Raw catalog, week-one review | In progress | Catalog, dbt sources, staging, `athena_query.py` and `transform-daily` merged (#13 to #15, #17, #18). Left: dev rebuild, `dbt build`/`source freshness` acceptance, week-one review. |
| M3 Disruption Index | Not started | |
| M4 Experienced Reliability | Not started | |
| M5 Publish pipeline | Not started | Needs H12 (Cloudflare). |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | Not started | |

## Open PRs and merge order

No other PRs are open. #17 then #18 merged on 2026-11-06, the `infra` deploy for #17 passed, and `transform-daily` has passed since.

In progress, no PR yet. Each branches from `main`, so none depends on another:

1. `feat/bootstrap-hardening`
2. `chore/deploy-safety`
3. `feat/ops-workflows`
4. `docs/working-agreement`: this page and DECISIONS.md. Docs only, merge in any order.

Add each PR number here when it opens.

## Blockers

- **Poller reserved concurrency of 1**: waits on the Lambda quota increase (ADR-006).
- **M2 acceptance**: waits on the dev rebuild below, then about 7 days of data for the week-one review.
- **ADR-009 and ADR-010**: need explicit approval (see DECISIONS.md). Nothing applies them by default.

## Owner steps outstanding

1. **AWS SSO session.** The `mtd-dev` token has expired, which blocks the dev rebuild and every AWS read:

   ```sh
   aws sso login --sso-session mtd
   ```

2. **Lambda quota.** The request for 1,000 shows `CASE_OPENED` (2026-11-06 02:54 UTC). Check it; if it shows `DENIED` or `CASE_CLOSED` below 1,000, request it again:

   ```sh
   aws service-quotas list-requested-service-quota-change-history-by-quota --profile mtd-dev --region eu-west-2 \
     --service-code lambda --quota-code L-B99A9384 --query 'RequestedQuotas[].[Status,DesiredValue]'
   aws service-quotas request-service-quota-increase --profile mtd-admin --region eu-west-2 \
     --service-code lambda --quota-code L-B99A9384 --desired-value 1000
   ```

3. **Local working config.** Install the proposed untracked project rules, hooks and `local-setup/overnight.sh`, following the install notes delivered with them. They hold the working agreement, the branch-from-`origin/main` rule and a session pre-flight check.
4. **Decisions.** Answer the items in DECISIONS.md before their deadlines. The `never-default` items wait for you.

Engineer step, waiting only on the SSO login now that #17 has deployed: rebuild dev staging.

```sh
make dbt-build-dev DBT_FLAGS="--profiles-dir . --full-refresh"
```

## Upcoming dated checks

| Date | Check |
| --- | --- |
| around 2026-11-11 | Week-one data-quality review (M2), including the ADR-002 decision on 60 s vs 30 s polling. |
| after 2026-11-10 | Re-run the station "arrivals strict" check during service hours. Suffragette line and tram closures leave some boards empty until then. |
