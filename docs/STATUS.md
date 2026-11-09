# Status

Updated: 2026-11-09. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 is in the M3 bundle (Part 0, ADR-006), now that the quota is 1,000. The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data: a dev full refresh passed 114 of 114 (2026-11-08). The autonomy bundle (#23) merged. Left: `dbt source freshness` in the record, and the week-one review. |
| M3 Disruption Index | In review | All four tasks in one bundle, #27 (`feat/m3-line-minute-metrics`). The four M3 boxes in the local plan are to be ticked in the main checkout (the copy is local-only). Decisions D16 to D24 wait for the owner. The league preview needs a complete week of data, the first being 2 to 8 November (complete at Monday 9 November 00:00 London). |
| M4 Experienced Reliability | In progress | Branch `feat/m4-measured-view`, one bundled PR later. All five tasks are built on dev: the reference, `int_inferred_arrivals` (identical to the reference on every arrival of 6 and 7 November), `int_scheduled_headways`, `int_headways` with excess wait, long gaps and the Honesty Gap in `fct_line_weekly`, and the quality gate. The week-one review is in `docs/data-quality/`. The acceptance is not met: 65.6% of core-station Tube hours are in band against 80% ([measured view](data-quality/2026-11-09-measured-view.md)). The branch also fixes the `stg_arrivals` key (D44). Decisions D25 to D34 and D39 to D44. |
| M5 Publish pipeline | Not started | Needs H12 (Cloudflare). |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | Not started | |

## Open PRs and merge order

- **M3 bundle** (#27, `feat/m3-line-minute-metrics`): the poller's reserved concurrency (Part 0), seeds, line-minute status, intervals, incidents, service hours, daily and weekly facts, and their unit tests. Nothing else is open, so it merges on its own. Merging deploys `infra/envs/prod` (one in-place change: the poller's reserved concurrency) and the next `transform-daily` run builds the new models in prod.
- Merged on 2026-11-07 and 2026-11-08: #17, #18, #22 (bootstrap hardening) and #23 (autonomy bundle).

## Blockers

- **M4 acceptance:** the quality gate is at 65.6% of core-station Tube hours (target 80%). Next: the Monday 9 November check to switch on vehicle-id linking for more Tube lines (D27), then re-run `docs/data-quality/2026-11-09-measured-view.md`.

- **M3 metric definitions:** D16 to D23 are `never-default`. The models implement the recommended reading of each; nothing is published until M5, so they can change with a seed edit and a rebuild.
- **M3 league preview:** returns a useful week only after Monday 9 November 00:00 London. Re-run it then (below).
- **M2 acceptance:** about 7 days of data for the week-one review, around 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

0. **After the M4 bundle merges**, rebuild prod `stg_arrivals` on the new key (D44), or it keeps short days and duplicate rows:

   ```sh
   cd transform
   AWS_PROFILE=mtd-admin uv run dbt build --target prod --profiles-dir . --full-refresh --select stg_arrivals+
   ```

   Needs the `DBT_ATHENA_*_PROD` settings in the shell (docs/runbook.md, dbt connection settings). Then answer D39 to D44 and the M4 parts of D25 to D28: the code runs the review's recommendations until then.

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

## Questions for the week-one review (M4)

The measured view depends on these. Each names the decision it feeds.

- **Is `vehicle_id` stable per train, line by line?** It sets `vehicle_id_lines` (D27). On 2026-11-05 at Oxford Circus and King's Cross, Victoria line linking by vehicle id gave 1,874 arrivals against 2,225 by the 45 s rule (D26).
- **How far does `expected_arrival` move between polls in the last 120 s, and how often does a held train's estimate move by more than 45 s a minute?** This decides whether the 45 s window stands (D26, ADR-002).
- **How often do duplicate predictions appear, and how far apart are they?** This sets the 15 s duplicate tolerance (D25).
- **How often does a train change platform on the board mid-approach** (D28)?
- **Do terminus boards list arriving trains that terminate?** There is no timetable for them (D33), so are they to be left out of headways?
- **DLR boards:** no vehicle ids, few predictions (Canning Town showed about 60 sightings an hour on a weekday morning), and one prediction id per platform and destination. Can DLR be measured at all? Are the Canning Town same-minute departures one train or two (D31)?
- **Overground boards:** `direction` is often null, especially at termini. Suffragette predictions are absent at Barking Riverside and Blackhorse Road during the closure. Which boards are usable for the fallback lines (D33)?

## Upcoming dated checks

| Date | Check |
| --- | --- |
| After 2026-11-10 | Re-run the station check during service hours: `uv run python scripts/discover_lines.py --check-arrivals --strict`. Suffragette line and tram closures leave some boards empty until then. |
| 2026-11-09 | First complete London week (2 to 8 November) for the M3 league preview. Its first two days predate the collector, so every line-week is provisional. |
| Around 2026-11-11 | Week-one data-quality review (M2), including the ADR-002 decision: keep 60 s polling or move to 30 s. |
| 2026-11-16 | First full week of data (9 to 15 November): the first non-provisional league. |
