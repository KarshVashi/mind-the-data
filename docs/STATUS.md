# Status

Updated: 2026-11-09. Every PR updates this page. Pending decisions are listed in [DECISIONS.md](DECISIONS.md).

## Milestones

| Milestone | State | Notes |
| --- | --- | --- |
| M0 Foundations | Done | |
| M1 Collector live | Done, with one gap | Polling since 2026-11-04. The poller's reserved concurrency of 1 shipped with M3 (#27, ADR-006). The deployed acceptance output (`completeness.py --hours 24`, alarm test) is not recorded in the repo yet. |
| M2 Raw catalog, week-one review | In progress | Staging verified on real data: a dev full refresh passed 114 of 114 (2026-11-08). The autonomy bundle (#23) merged. Left: `dbt source freshness` in the record, and the week-one review. |
| M3 Disruption Index | Done | Merged as #27 on 2026-11-08; the deploy and a `transform-daily` run in prod succeeded the same evening. Decisions D16 to D24 wait for the owner. The first complete London week is 2 to 8 November. |
| M4 Experienced Reliability | Built, gate not passed | In the M4/M5 bundle (`feat/m4-m5-measured-view-and-publish`). All five tasks are built on dev: the reference, `int_inferred_arrivals` (identical to the reference on every arrival of 6 and 7 November), `int_scheduled_headways`, `int_headways` with excess wait, long gaps and the Honesty Gap in `fct_line_weekly`, and the quality gate. The week-one review is in `docs/data-quality/`. The acceptance is not met: 65.6% of core-station Tube hours are in band against 80% ([measured view](data-quality/2026-11-09-measured-view.md)), so the gate test warns (D44) and the measured view is not published. The bundle also fixes the `stg_arrivals` key (D45). Decisions D25 to D34 and D40 to D45. |
| M5 Publish pipeline | Offline parts built, blocked on H12 | In the same bundle: JSON contract and `export.py` (with dry run), `meta/index.json`, share cards, `scripts/post_snapshot.py` for custom windows, and the website plan in `docs/site-integration.md`. Exports carry the declared view only. Not built: the R2 upload, the Pages deploy hook and `publish-weekly.yml`, which need H12 (Cloudflare). Decisions D35 to D39. |
| M6 League mechanics | Not started | |
| M7 Hardening and docs | Not started | |

## Open PRs and merge order

- **M4/M5 bundle** (`feat/m4-m5-measured-view-and-publish`): the measured view (M4, gate not passed), the `stg_arrivals` key fix (D45), and the offline M5 publish work. Nothing else is open, so it merges on its own. It changes no Terraform, so the deploy is a no-op; the next `transform-daily` run builds the M4 models in prod, after the full refresh below.
- Merged: #17, #18, #22 (bootstrap hardening), #23 (autonomy bundle) and #27 (M3, 2026-11-08).

## Blockers

- **M4 acceptance:** the quality gate is at 65.6% of core-station Tube hours (target 80%). The measured view is not published until it passes. Next: the check to switch on vehicle-id linking for more Tube lines (D27), then re-run `docs/data-quality/2026-11-09-measured-view.md`.
- **M5 upload and deploy:** blocked on H12 (Cloudflare account, R2 bucket and token, Pages deploy hook). Until then exports and cards are written locally only.
- **M3 metric definitions:** D16 to D23 are `never-default`. The models implement the recommended reading of each, so they can change with a seed edit and a rebuild. The first post (D36) rests on them.
- **M2 acceptance:** about 7 days of data for the week-one review, around 2026-11-11.
- **Prod staging may still hold null-keyed rows** from runs before #17. See decision D8; the D45 full refresh below rebuilds `stg_arrivals` from raw anyway.
- **ADRs and decisions D1 to D15:** each needs an explicit answer (DECISIONS.md).

## Owner steps outstanding

1. **After the M4/M5 bundle merges**, rebuild prod `stg_arrivals` on the new key (D45), or it keeps short days and duplicate rows. Needs the admin profile and the `DBT_ATHENA_*_PROD` settings in the shell (docs/runbook.md, dbt connection settings):

   ```sh
   cd transform
   AWS_PROFILE=mtd-admin uv run dbt build --target prod --profiles-dir . --full-refresh --select stg_arrivals+
   ```

2. Then rebuild everything else in prod through CI:

   ```sh
   gh workflow run transform-daily.yml --ref main
   ```

3. **Wednesday 11 November**, the first post's snapshot, from a clean checkout of `main` (so `git_sha` has no `-dirty`). Check `numbers.md` before posting (runbook, "Snapshot for a post"), and settle D36 first:

   ```sh
   AWS_PROFILE=mtd-dev uv run python scripts/post_snapshot.py --start 2026-11-04 --end 2026-11-10 --out-dir ~/mtd-snapshots/2026-11-11
   ```

4. **Decisions.** Answer the items in DECISIONS.md: before Wednesday D19, D22, D17 and D36; then the rest. The `never-default` items wait for you.

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
