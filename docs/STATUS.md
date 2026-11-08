# Status

Updated: 2026-11-08. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 is in the M3 bundle (Part 0, ADR-006), now that the quota is 1,000. The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data: a dev full refresh passed 114 of 114 (2026-11-08). The autonomy bundle (#23) merged. Left: `dbt source freshness` in the record, and the week-one review. |
| M3 Disruption Index | In review | All four tasks in one bundle, #27 (`feat/m3-line-minute-metrics`). The four M3 boxes in the local plan are to be ticked in the main checkout (the copy is local-only). Decisions D16 to D24 wait for the owner. The league preview needs a complete week of data, the first being 2 to 8 November (complete at Monday 9 November 00:00 London). |
| M4 Experienced Reliability | Not started | |
| M5 Publish pipeline | In progress | Offline parts on `feat/m5-publish`: JSON contract and `export.py`, share cards, `scripts/post_snapshot.py` for custom windows. R2 upload, the Pages hook and `publish-weekly.yml` need H12 (Cloudflare); M5 ships as one PR once they are added. Decisions D25 and D26. |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | Not started | |

## Open PRs and merge order

- **M3 bundle** (#27, `feat/m3-line-minute-metrics`): the poller's reserved concurrency (Part 0), seeds, line-minute status, intervals, incidents, service hours, daily and weekly facts, and their unit tests. Nothing else is open, so it merges on its own. Merging deploys `infra/envs/prod` (one in-place change: the poller's reserved concurrency) and the next `transform-daily` run builds the new models in prod.
- Merged on 2026-11-07 and 2026-11-08: #17, #18, #22 (bootstrap hardening) and #23 (autonomy bundle).

## Blockers

- **M3 metric definitions:** D16 to D23 are `never-default`. The models implement the recommended reading of each; nothing is published until M5, so they can change with a seed edit and a rebuild.
- **M3 league preview:** returns a useful week only after Monday 9 November 00:00 London. Re-run it then (below).
- **M2 acceptance:** about 7 days of data for the week-one review, around 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

1. **Decisions.** Answer the items in DECISIONS.md, in particular D16 to D23 for M3. The `never-default` items wait for you.
2. **After the M3 bundle merges**, in order:

   ```sh
   gh run list --workflow infra.yml --limit 1          # the deploy: one in-place change on the poller, smoke test green
   aws lambda get-function-concurrency --profile mtd-dev --region eu-west-2 \
     --function-name mind-the-data-poller             # expect ReservedConcurrentExecutions: 1
   gh workflow run transform-daily.yml --ref main      # builds the M3 models in prod
   ```

3. **On or after Monday 9 November**, re-run the league preview on dev (engineer step, no owner access needed):

   ```sh
   make dbt-build-dev
   cd transform && uv run --project .. dbt compile --profiles-dir . --target dev --select league_preview
   uv run python scripts/athena_query.py --file transform/target/compiled/mind_the_data/analyses/league_preview.sql
   ```

## Upcoming dated checks

| Date | Check |
| --- | --- |
| After 2026-11-10 | Re-run the station check during service hours: `uv run python scripts/discover_lines.py --check-arrivals --strict`. Suffragette line and tram closures leave some boards empty until then. |
| 2026-11-09 | First complete London week (2 to 8 November) for the M3 league preview. Its first two days predate the collector, so every line-week is provisional. |
| Around 2026-11-11 | Week-one data-quality review (M2), including the ADR-002 decision: keep 60 s polling or move to 30 s. |
| 2026-11-16 | First full week of data (9 to 15 November): the first non-provisional league. |
