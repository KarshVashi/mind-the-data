# Status

Updated: 2026-11-13. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 shipped with M3 (#27, ADR-006). The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data. The early cut of the week-one review is in [docs/data-quality/2026-11-08-week-one.md](data-quality/2026-11-08-week-one.md) and answers the M4 questions (Q2 to Q7). Left: the full-week re-run on fixed staging (D45) and `dbt source freshness` in the record. |
| M3 Disruption Index | Done | Merged as #27 on 2026-11-08; the deploy and a `transform-daily` run in prod succeeded the same evening. Decisions D16 to D24 wait for the owner. The first complete London week is 2 to 8 November. |
| M4 Experienced Reliability | Built, gate not passed | Merged in the M4/M5 bundle (#28). All five tasks are built on dev: the reference, `int_inferred_arrivals` (identical to the reference on every arrival of 6 and 7 November), `int_scheduled_headways`, `int_headways` with excess wait, long gaps and the Honesty Gap in `fct_line_weekly`, and the quality gate. The week-one review is in `docs/data-quality/`. The acceptance is not met: 65.6% of core-station Tube hours are in band against 80% ([measured view](data-quality/2026-11-09-measured-view.md)), so the gate test warns (D44) and the measured view is not published. The bundle also fixes the `stg_arrivals` key (D45). Decisions D25 to D34 and D40 to D45. |
| M5 Publish pipeline | Built, blocked on H12 | The contract, `export.py`, `meta/index.json`, share cards, `scripts/post_snapshot.py` and `docs/site-integration.md` merged in #28. The R2 upload (`publish/upload.py`), the Pages deploy hook call and `publish-weekly.yml` are in the M5/M7 bundle (`feat/m5-m7-publish-and-docs`). Exports carry the declared view only. Left: H12 (Cloudflare) and the acceptance's `dry_run` run on `main`. Decisions D35 to D39. |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | In progress | In the M5/M7 bundle: `docs/methodology.md`, `docs/architecture.md`, the README, and runbook sections for H12 and the weekly publish. Left: weekly Iceberg maintenance, the monthly open dataset, `docs/cost.md` after the first full month, and the rest of the runbook (re-runs, backfilling marts, key rotation). |

## Open PRs and merge order

- **M5/M7 bundle** (`feat/m5-m7-publish-and-docs`): the R2 upload, deploy hook and `publish-weekly.yml` (M5), and the methodology, architecture and README docs (M7). No Terraform change: it reuses `mtd-ci-publish`, which the bootstrap already created and bound to `publish-weekly.yml`. `src/` changes, so `infra.yml` rebuilds the Lambda package and updates both Lambdas in place. It touches `.github/`, so it needs the owner to merge.
- Merged: #17, #18, #22, #23, #27 (M3, 2026-11-08) and #28 (M4/M5 bundle).

## Blockers

- **M4 acceptance:** the quality gate is at 65.6% of core-station Tube hours (target 80%). The measured view is not published until it passes. Next: the check to switch on vehicle-id linking for more Tube lines (D27), then re-run `docs/data-quality/2026-11-09-measured-view.md`.
- **M5 upload and deploy:** blocked on H12 (Cloudflare R2 bucket, token and custom domain, Pages deploy hook, five GitHub secrets; runbook, H12) and on D36. Until then `publish-weekly.yml` skips with a notice, and a manual `dry_run` run keeps the export as an artifact.
- **M3 metric definitions:** D16 to D23 are `never-default`. The models implement the recommended reading of each, so they can change with a seed edit and a rebuild. The first post (D36) rests on them.
- **M2 acceptance:** the full-week re-run of the week-one review on fixed staging, from 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8; the D45 full refresh below rebuilds `stg_arrivals` from raw anyway.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

1. **After the M4/M5 bundle merges**, check the deploy: two in-place Lambda code updates and a green smoke test.

   ```sh
   gh run list --workflow infra.yml --limit 1
   ```

2. **Before the next 03:30 London `transform-daily` run**, rebuild prod `stg_arrivals` on the new key (D45). Until this runs, the daily run rereads two days under the new key beside the old rows, `assert_staged_arrivals_match_raw` fails, and the models downstream of `stg_arrivals` are skipped, including `fct_line_weekly` (it now carries the measured columns), which stays at its last build. `fct_line_daily` and `post_snapshot.py` are not affected. The refresh runs locally as the admin profile this once because `transform-daily.yml` has no full-refresh input yet (D8 prefers a one-off workflow run; adding that input is a follow-up). Needs the `DBT_ATHENA_*_PROD` settings in the shell (docs/runbook.md, dbt connection settings):

   ```sh
   cd transform
   AWS_PROFILE=mtd-admin uv run dbt build --target prod --profiles-dir . --full-refresh --select stg_arrivals+
   ```

3. Then rebuild everything else in prod through CI:

   ```sh
   gh workflow run transform-daily.yml --ref main
   ```

4. **Wednesday 11 November**, the first post's snapshot, from a clean checkout of `main` (so `git_sha` has no `-dirty`). Settle D36 first and check `numbers.md` before posting (runbook, "Snapshot for a post"):

   ```sh
   AWS_PROFILE=mtd-dev uv run python scripts/post_snapshot.py --start 2026-11-04 --end 2026-11-10 --out-dir ~/mtd-snapshots/2026-11-11
   ```

5. **Decisions.** Answer the items in DECISIONS.md: before Wednesday D19, D22, D17 and D36; then the rest. The `never-default` items wait for you.

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
| 2026-11-16 | First full week of data (9 to 15 November): the first non-provisional league. |
