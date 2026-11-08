# Status

Updated: 2026-11-07. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller is unreserved until the quota rises (ADR-006). The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Merged: the catalog, dbt sources, staging, `athena_query.py` and `transform-daily` (#13 to #15, #17, #18). Left: the dev rebuild, the `dbt build` / `source freshness` acceptance, and the week-one review. |
| M3 Disruption Index | Not started | |
| M4 Experienced Reliability | Not started | |
| M5 Publish pipeline | Not started | Needs H12 (Cloudflare). |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | Not started | |

## Open PRs and merge order

- This PR (`docs/working-agreement`): docs only, so it can merge in any order.
- `feat/bootstrap-hardening`, `chore/deploy-safety` and `feat/ops-workflows` are in progress with no PR yet. Each branches from `main`. Their merge order goes here when they open.
- Merged on 2026-11-06: #17, then #18. The `infra` deploy for #17 passed, and `transform-daily` has passed since.

## Blockers

- **Poller reserved concurrency of 1:** waits on the Lambda quota increase (ADR-006).
- **M2 acceptance:** waits on the dev rebuild below, then on about 7 days of data for the week-one review.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8 in DECISIONS.md.
- **ADR-006 to ADR-010:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

1. **AWS SSO session.** The `mtd-dev` token has expired, which blocks the dev rebuild and every AWS read.

   ```sh
   aws sso login --sso-session mtd
   ```

2. **Lambda quota.** The request for 1,000 is `CASE_OPENED` (2026-11-06 02:54 UTC). To check it, or to request again if it is denied, see the runbook section "Lambda concurrency quota".
3. **Decisions.** Answer the items in DECISIONS.md. The `never-default` items wait for you.

Engineer step, which only needs the SSO login now that #17 has deployed: rebuild dev staging.

```sh
make dbt-build-dev DBT_FLAGS="--profiles-dir . --full-refresh"
```

## Upcoming dated checks

| Date | Check |
| --- | --- |
| After 2026-11-10 | Re-run the station check during service hours: `uv run python scripts/discover_lines.py --check-arrivals --strict`. Suffragette line and tram closures leave some boards empty until then. |
| Around 2026-11-11 | Week-one data-quality review (M2), including the ADR-002 decision: keep 60 s polling or move to 30 s. |
