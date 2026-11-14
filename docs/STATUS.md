# Status

Updated: 2026-11-14. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 shipped with M3 (#27, ADR-006). The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data. The early cut of the week-one review is in [docs/data-quality/2026-11-08-week-one.md](data-quality/2026-11-08-week-one.md) and answers the M4 questions (Q2 to Q7). Left: the full-week re-run on fixed staging (D45) and `dbt source freshness` in the record. |
| M3 Disruption Index | Done | Merged as #27 on 2026-11-08; the deploy and a `transform-daily` run in prod succeeded the same evening. The owner settled D19 (time-weighted denominator, 0 to 800) and D22 (ranked only if open half the scheduled hours) on 2026-11-14; both are built in the M6 and gate bundle. D16 to D18, D20, D21, D23 and D24 wait for the owner. |
| M4 Experienced Reliability | Built; gate passes under ADR-014 | Merged in the M4/M5 bundle (#28). The [recheck](data-quality/2026-11-12-gate-recheck.md) showed the timetable band confused trains not run with inference error. ADR-014 (proposed, D46, in the M6 and gate bundle) gates inferred trains against the trains on the boards: **87.1%** of judged core-station Tube hours in band on 9 to 13 November against the 80% target ([fidelity gate](data-quality/2026-11-14-fidelity-gate.md)); the old band gave 65.4%. The gate test passes on dev but stays at `warn` until the owner answers D44. The measured view, now with trains run against the timetable, is still not published (D42). Decisions D25 to D34, D40 to D46. |
| M5 Publish pipeline | Built, blocked on H12 | The contract, `export.py`, `meta/index.json`, share cards, `scripts/post_snapshot.py` and `docs/site-integration.md` merged in #28; the R2 upload, the Pages deploy hook call and `publish-weekly.yml` in #30. The owner settled D36 (publish with provisional labels) on 2026-11-14. Exports carry the declared view only. Left: H12 (Cloudflare) and the acceptance's `dry_run` run on `main`. Decisions D35, D37 to D39. |
| M6 League mechanics | In progress | The M6 and gate bundle (`feat/m6-league-and-gate`) builds the Wooden Spoon, Line of the Week and single worst incident (`rpt_weekly_highlights`), the Monday Morning Index (`rpt_monday_morning`) and the Engineering Works table (`rpt_engineering_works`), with hand-computed unit tests, and adds them to the export (`schema_version` 0.3.0). The rest of M6 (`feat/collate-b-m6`, into the same bundle) adds divisions seeded from two complete weeks, provisional until four exist (ADR-015; `rpt_division_seeding`, `rpt_divisions_monthly`; "not seeded" until W47 completes on 22 November, first division month December), the season table (`rpt_season`, April reset), the Recovery Time table (`rpt_recovery_time`, provisional on D18), the industrial action seed with sources and the strikes-excluded league (`rpt_league_strikes_excluded`), and the sensitivity analysis in `meta/methodology.json` (`rpt_weekly_status_mix`). Export: `divisions/` and `season/` files (`schema_version` 0.4.0). Decisions D47 to D50, D56 to D61. |
| M7 Hardening and docs | In progress | In the M5/M7 bundle: `docs/methodology.md`, `docs/architecture.md`, the README, and runbook sections for H12 and the weekly publish. Left: weekly Iceberg maintenance, the monthly open dataset, `docs/cost.md` after the first full month, and the rest of the runbook (re-runs, backfilling marts, key rotation). |

## Open PRs and merge order

- **M6 and gate bundle** (`feat/m6-league-and-gate`): D19, D22 and D36 as decided, ADR-014 with the new gate, and the first M6 outputs. It carries the 12 November recheck page (`fix/m4-gate-vehicle-id-lines`), which can be closed once this merges. No Terraform or workflow change; `src/` changes, so `infra.yml` rebuilds the Lambda package and updates both Lambdas in place.
- Merged: #17, #18, #22, #23, #27 (M3, 2026-11-08), #28 (M4/M5 bundle) and #30 (M5/M7 bundle).

## Blockers

- **M4 acceptance:** met on the ADR-014 definition (87.1%), which needs the owner to accept ADR-014 (D46). Then D44 (`warn` or `error`) and D42 (publish the measured view). Piccadilly (74.1%) and Metropolitan (75.5%) are under 80% on their own; linking them by vehicle id (D27) is the recheck's fix.
- **Circle and Hammersmith & City** misfile trains on the shared section; assigning them by vehicle-id history is a follow-up in ADR-014. Their trains-run shares are withheld until then.
- **M5 upload and deploy:** blocked on H12 (Cloudflare R2 bucket, token and custom domain, Pages deploy hook, five GitHub secrets; runbook, H12). D36 is settled. Until H12, `publish-weekly.yml` skips with a notice, and a manual `dry_run` run keeps the export as an artifact. The schedule publishes only once `PUBLISH_ENABLED` is set (runbook, H12 step 9).
- **M3 metric definitions:** D16 to D18, D20, D21 and D23 are `never-default`. The models implement the recommended reading of each, so they can change with a seed edit and a rebuild.
- **M2 acceptance:** the full-week re-run of the week-one review on fixed staging, from 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8; the D45 full refresh below rebuilds `stg_arrivals` from raw anyway.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

After the M6 and gate bundle merges, in order:

1. **Check the deploy:** two in-place Lambda code updates and a green smoke test.

   ```sh
   gh run list --workflow infra.yml --limit 1
   ```

2. **Rebuild prod through CI.** The new models (`int_vehicle_passes`, the `rpt_` models) and the changed marts build in full; staging is unchanged, so no full refresh is needed.

   ```sh
   gh workflow run transform-daily.yml --ref main
   gh run list --workflow transform-daily.yml --limit 1
   ```

   If the D45 full refresh of prod `stg_arrivals` from the M4/M5 bundle has not been run yet, run it first (`AWS_PROFILE=mtd-admin uv run dbt build --target prod --profiles-dir . --full-refresh --select stg_arrivals+` from `transform/`, with the `DBT_ATHENA_*_PROD` settings).

3. **Check the export** from a clean checkout of `main`, without publishing:

   ```sh
   gh workflow run publish-weekly.yml --ref main -f dry_run=true
   ```

4. **Decisions.** D46 (accept ADR-014), then D44 (`gate_test_severity: error`?) and D42 (publish the measured view); D27 (link the eight lines by vehicle id); D47 to D50 (the M6 definitions); and the rest of the inbox.
5. **H12** (Cloudflare) and `PUBLISH_ENABLED`, when you want the Monday schedule to publish (runbook, H12). The first full week, W46, is complete on Monday 16 November.

## Week-one review answers (M4)

The early cut of the review ([docs/data-quality/2026-11-08-week-one.md](data-quality/2026-11-08-week-one.md)) answers the questions the measured view depends on, and the code follows its recommendations pending the owner:

- `vehicle_id` stability (Q2): D27, D26.
- Duplicate predictions (Q3): D25.
- Estimate drift in the last 120 s (Q4): D40, D26 and ADR-002.
- Empty or odd boards (Q5): D45.
- Overground, DLR and trams (Q6): D33, D43, D31.
- Terminus boards and platform changes (Q7): D28.

The measured-view review ([docs/data-quality/2026-11-09-measured-view.md](data-quality/2026-11-09-measured-view.md)) records the gate result.

## Upcoming dated checks

| Date | Check |
| --- | --- |
| After 2026-11-10 | Re-run the station check during service hours: `uv run python scripts/discover_lines.py --check-arrivals --strict`. Suffragette line and tram closures leave some boards empty until then. |
| 2026-11-09 | First complete London week (2 to 8 November) for the M3 league preview. Its first two days predate the collector, so every line-week is provisional. |
| From 2026-11-11 | Full-week re-run of the week-one review on fixed staging (M2), including the ADR-002 decision: keep 60 s polling or move to 30 s. |
| 2026-11-16 | First full week of data (9 to 15 November): the first non-provisional league. No Line of the Week (the week before was partial, D47). |
| 2026-11-23 | W47 complete: the first Line of the Week. |
| 2026-11-26 | First strike day: the strikes-excluded variant (M6) is a follow-up. |
