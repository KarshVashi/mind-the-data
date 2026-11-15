# Status

Updated: 2026-11-14. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 shipped with M3 (#27, ADR-006). The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data. The early cut of the week-one review is in [docs/data-quality/2026-11-08-week-one.md](data-quality/2026-11-08-week-one.md) and answers the M4 questions (Q2 to Q7). Left: the full-week re-run on fixed staging (D45) and `dbt source freshness` in the record. |
| M3 Disruption Index | Done | Merged as #27 on 2026-11-08; the deploy and a `transform-daily` run in prod succeeded the same evening. The owner settled D19 (time-weighted denominator, 0 to 800) and D22 (ranked only if open half the scheduled hours) on 2026-11-14; both are built in the M6 and gate bundle. D16 to D18, D20, D21, D23 and D24 wait for the owner. |
| M4 Experienced Reliability | Built; gate passes under ADR-014 | Merged in the M4/M5 bundle (#28). The [recheck](data-quality/2026-11-12-gate-recheck.md) showed the timetable band confused trains not run with inference error. ADR-014 (accepted 2026-11-14, D46) gates inferred trains against the trains on the boards: **87.1%** of judged core-station Tube hours in band on 9 to 13 November against the 80% target ([fidelity gate](data-quality/2026-11-14-fidelity-gate.md)); the old band gave 65.4%. The gate test runs at `error` (D44) and skips only the measured models (D55): the measured view per line-week lives in `fct_line_weekly_measured`, apart from the declared `fct_line_weekly`. The measured view is **published** per line-week when the line's own gate passes (D42, D51); Circle and Hammersmith & City are withheld (D52), the Elizabeth line and Overground are weak evidence and unranked (D53), and the Honesty Gap stays off the share card (D54). Open: D25 to D34, D40, D41, D43, D45, D51 to D54. |
| M5 Publish pipeline | Built, blocked on H12 | The contract, `export.py`, `meta/index.json`, share cards, `scripts/post_snapshot.py` and `docs/site-integration.md` merged in #28; the R2 upload, the Pages deploy hook call and `publish-weekly.yml` in #30. The owner settled D36 (publish with provisional labels) on 2026-11-14. Exports carry both views (contract 0.5.0). Left: H12 (Cloudflare) and the acceptance's `dry_run` run on `main`. Decisions D35, D37 to D39, D54. |
| M6 League mechanics | Built, in the bundle | The bundle (`feat/m6-league-and-gate`, #32) builds the Wooden Spoon, Line of the Week and single worst incident (`rpt_weekly_highlights`), the Monday Morning Index (`rpt_monday_morning`), the Engineering Works table (`rpt_engineering_works`), divisions seeded from two complete weeks and provisional until four exist (ADR-015; `rpt_division_seeding`, `rpt_divisions_monthly`; "not seeded" until W47 completes on 22 November, first division month December), the season table (`rpt_season`, April reset), the Recovery Time table (`rpt_recovery_time`, provisional on D18), the industrial action seed with sources and the strikes-excluded league (`rpt_league_strikes_excluded`), and the sensitivity analysis in `meta/methodology.json` (`rpt_weekly_status_mix`), all with hand-computed unit tests. Export: `divisions/` and `season/` files. D47 to D50 settled; D56 to D61 open. |
| M7 Hardening and docs | Built, waiting on decisions | `docs/methodology.md`, `docs/architecture.md` and the README merged in #30. The bundle adds weekly Iceberg maintenance on Sundays (D64), a guarded `full_refresh` input on `transform-daily` (D8, D45 run in CI), the monthly open dataset as Parquet (`open-data/`, D63; contract 0.5.0 adds its manifest), [docs/cost.md](cost.md) (partial month: about 1.80 USD a month projected before VAT) and the rest of the runbook (alarms, re-runs, backfills, full refresh, key rotation, gate failures, the industrial action seed). Left: the full-month cost figures after 1 December, `dbt build --target prod` in the acceptance, D63 and H12 before the open data goes out. |

## Open PRs and merge order

- **#32, the M4 to M7 bundle** (`feat/m6-league-and-gate`): D19, D22, D36, D42, D44, D46 to D50 and D55 as decided; ADR-014 with the new gate; the measured view published (`fct_line_measured_daily`, `fct_line_weekly_measured`); the rest of M6 (ADR-015); the rest of M7; contract 0.5.0. Open decisions it adds: D51 to D54, D56 to D61, D63, D64. It carries the 12 November recheck page (`fix/m4-gate-vehicle-id-lines`), which can be closed once this merges. A workflow change (`transform-daily.yml`: `full_refresh` input, Sunday maintenance) and `src/` changes, so `infra.yml` rebuilds the Lambda package and updates both Lambdas in place.
- Merged: #17, #18, #22, #23, #27 (M3, 2026-11-08), #28 (M4/M5 bundle) and #30 (M5/M7 bundle).

## Blockers

- **M4 acceptance:** met on the ADR-014 definition (87.1%); ADR-014, D42 and D44 settled on 2026-11-14. Piccadilly (74.1%) and Metropolitan (75.5%) are under 80% on their own but inside the line-week limit (D51); linking them by vehicle id (D27) is the recheck's fix.
- **Circle and Hammersmith & City** misfile trains on the shared section; assigning them by vehicle-id history is a follow-up in ADR-014 (needs cross-station state in the Python reference and `int_inferred_arrivals`, with parity fixtures from Baker Street, King's Cross and Liverpool Street). Their whole measured view is withheld until then (D52).
- **M5 upload and deploy:** blocked on H12 (Cloudflare R2 bucket, token and custom domain, Pages deploy hook, five GitHub secrets; runbook, H12). D36 is settled. Until H12, `publish-weekly.yml` skips with a notice, and a manual `dry_run` run keeps the export as an artifact. The schedule publishes only once `PUBLISH_ENABLED` is set (runbook, H12 step 9).
- **M3 metric definitions:** D16 to D18, D20, D21 and D23 are `never-default`. The models implement the recommended reading of each, so they can change with a seed edit and a rebuild.
- **M2 acceptance:** the full-week re-run of the week-one review on fixed staging, from 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8; the D45 full refresh below rebuilds `stg_arrivals` from raw anyway.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

After #32 merges, in order:

1. **Check the deploy:** two in-place Lambda code updates and a green smoke test.

   ```sh
   gh run list --workflow infra.yml --limit 1
   ```

2. **If the D45 full refresh of prod `stg_arrivals` has not been run yet,** run it now through CI (runbook, "Full refresh in prod"). It also rebuilds every model downstream.

   ```sh
   gh workflow run transform-daily.yml --ref main -f full_refresh=true -f select='stg_arrivals+'
   ```

3. **Rebuild prod through CI.** No staging key changed in this bundle, so no other full refresh is needed. The new models (`int_vehicle_passes`, `fct_line_measured_daily`, `fct_line_weekly_measured`, the `rpt_` models) and the changed marts build in full. The export reads the new tables, so it fails until this has run once with the gate passing (`fct_line_weekly_measured` must exist).

   ```sh
   gh workflow run transform-daily.yml --ref main
   gh run list --workflow transform-daily.yml --limit 1
   ```

4. **Check the export** from a clean checkout of `main`, without publishing:

   ```sh
   gh workflow run publish-weekly.yml --ref main -f dry_run=true
   ```

5. **Decisions.** D51 to D54 (how the measured view is published), D56 to D61 (M6), D63 (open data licence), D64 (auto-apply), D18, D27 and the rest of the inbox.
6. **H12** (Cloudflare), then set `PUBLISH_ENABLED` when you want the Monday schedule to publish (runbook, H12 step 9).
7. **`OPEN_DATA_ENABLED`**, only after D63 is settled and H12 is done (runbook, "Monthly open data").

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
| 2026-11-23 | W47 complete: the first Line of the Week, and the divisions are seeded (provisional, ADR-015) to play from December. |
| 2026-11-26 | First announced strike day (26 to 27 November). By 1 December set its seed row to `held` or `called_off`; the daily ops report flags it from 1 December (runbook, "Industrial action seed"). |
| 2026-12-02 | First monthly open data run (November), once D63, H12 and `OPEN_DATA_ENABLED` are done. |
