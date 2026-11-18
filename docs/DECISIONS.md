# Decisions inbox

Decisions waiting on the owner. Every PR updates this page. When a decision is settled, move it to "Settled" with the date and the outcome.

## Classes

- `auto-apply`: if the owner says nothing by the deadline, the recommended default applies and is recorded under "Settled".
- `never-default`: nothing happens until the owner answers. This covers:
  - security boundaries, IAM or OIDC;
  - money or quotas;
  - metric semantics;
  - deviations from the delivery plan (kept privately, outside this repo);
  - publishing;
  - data deletion.

  For these, the deadline is only a reminder. The item stays pending until it is answered.

## Deadlines

The deadline is 72 hours after the item enters this inbox. Unless a section says otherwise, an item below entered on 2026-11-07, so its deadline was 2026-11-10 12:00 UTC.

## Pending

D1 and D67 are open.

### D67. Single-weekday timetables are left out until a weekday can be modelled

- **Entered:** 2026-11-18. **Deadline:** 2026-11-21 18:00 UTC. **Class:** `auto-apply` (no published figure moves). **Default:** as implemented.
- **Context:** from 18 November 2026 TfL sends single-weekday schedules ("Tuesdays", "Wednesdays", "Thursdays") in the timetables of some District and Metropolitan stops, found by the daily reference job (ADR-016). The stops are Barbican, Euston Square, Wembley Park, Rickmansworth and Watford on the Metropolitan, and West Brompton and East Ham on the District. `assert_timetable_schedules_are_mapped` failed, and the transform skipped every model downstream of `stg_timetable`. The 12:06 and 17:05 UTC runs on 18 Nov both failed this way.
- **Why not map them to `mon_thu`:** `int_scheduled_headways` sums every schedule of a day type in a timetable version. A stop with "Tuesdays", "Wednesdays" and "Thursdays" would get three days of trains on each of those days.
- **Implemented:** seed `timetable_schedules_unmodelled` lists the names, each with a reason. The test accepts them, and the model drops their departures as it drops any unmapped name. Those stop-directions have no scheduled service on days that use such a version, so they are not measured then. That is missing data, never Good Service. None is a measurement station, so the league, the measured view and W46 do not change; only the unpublished station views do.
- **Follow-up:** model a schedule that runs on one weekday, for example a nullable `iso_day_of_week` in `timetable_schedule_day_types`, carried through `int_scheduled_headways` and matched by the measured-hours models.


## Settled on 2026-11-18, with the detail

### D65. A prediction with no direction takes one where the boards show it

- **Entered:** 2026-11-18. **Class:** `never-default` (it changes how a board is formed). **Settled 2026-11-18 (owner):** accepted as implemented.
- **Context:** TfL lists many predictions with no direction. In W46, 79% of the Metropolitan's on Liverpool Street westbound platform 2 and 53% at King's Cross westbound had none. So did most at termini, and those of trains terminating at the stop. The inference keys a board on direction (D28), and the headways and vehicle passes leave a null direction out, so such trains were neither counted nor given an arrival.
- **Implemented** (`macros/fill_missing_direction.sql`; views `int_arrivals_directed` and `int_station_arrivals_directed`, which the inference now reads instead of the staging tables). A missing direction is taken, in order:
  1. from the same vehicle id's other sightings on the same request, stop, line and platform that UTC day, when they all list one direction. Only on the lines `seed_vehicle_id_lines` links by vehicle id (D27).
  2. from the platform, when of its predictions that day that list a direction, at least 95% (and at least 20) list the same one (`direction_fill_platform_share`, `direction_fill_min_predictions`). Not at a turnback, a stop that is the destination of any of the line's trains that day. One short-turned train turns the rule off at that stop for the day, which is conservative.
  - Never filled: a train terminating at the stop, or a prediction on "Platform Unknown".
  - Every lookup is per UTC day, so reruns give the same rows. `direction_source` records the rule.
- **Versions tried and dropped:**
  - Filling terminating trains pushed the Overground's trains-on-the-boards share from about 110% to 160–290%: an arriving train that reverses counted twice.
  - Filling from the platform at turnbacks pushed District outbound at Barking to 1.21 trains per timetabled train. Most direction-less predictions have no destination, so a train ending its trip there cannot be told from one passing through.
  - The vehicle rule on lines with unstable ids carries a direction across trains.
- **Parity:** the Python reference (`analysis/arrivals.py`) and the SQL inference now both start from the filled rows (`scripts/check_arrival_parity.py` reads `int_arrivals_directed`). The fill itself is checked by the dbt unit test `int_arrivals_directed_fills_a_missing_direction`, not by the reference. Parity on 12 Nov at Liverpool Street, King's Cross, Earl's Court and Barking, with the final rules: identical, 7,003 arrivals.
- **Cost:** the view reads its staging table up to four times (the predictions, the vehicle lookup, the turnbacks and the platform counts). The reading models' `dt` bounds reach every read: one day through `int_arrivals_directed` scans 19 MB, and one day through `int_station_arrivals_directed` scans 80 MB. All history since 4 November through `int_arrivals_directed` scans 227 MB, well under the 5 GB point at which D41 revisits the full rebuild. If the line feed's backfills near the 10 GiB workgroup cap, the per-day lookups can become a small incremental table.
- **Effect on W46:** small on its own. The Metropolitan's trains on the boards rise from 67.5% to 68.9%, and its excess wait falls from 3.76 to 3.17 minutes. Every other line moves by under a point. The big change is D66.

### D66. Trains run is measured at core and branch stations, not termini

- **Entered:** 2026-11-18. **Class:** `never-default` (a published metric's definition). **Settled 2026-11-18 (owner):** accepted as implemented.
- **Context:** trains on the boards (ADR-014) summed every measurement station, termini included.
  - At some termini TfL lists departing trains with no direction, so they never count: Uxbridge on the Metropolitan read 0.00 trains per timetabled train, Wimbledon on the District 0.01, Amersham 0.12.
  - At Overground termini arriving trains are listed on more than one platform, so they count over: Stratford on the Mildmay read 1.8, Richmond 2.3.
  - The line-week gate already leaves termini out for this reason (D33, D51). The published headline "Only 68% of the Metropolitan line's timetabled trains showed on TfL's arrival boards" was mostly this effect.
- **Implemented** (`fct_line_measured_daily`): `observed_trains` and `observed_hours_scheduled_trains` sum observed hours at core and branch stations only. A line with only termini (Liberty) has no trains-run share.
- **Effect on W46, trains on the boards before → after D65 and D66:**

  | Line | Before | After |
  | --- | --- | --- |
  | Metropolitan | 67.5% | 76.7% |
  | District | 68.1% | 74.0% |
  | Jubilee | 83.0% | 84.8% |
  | Piccadilly | 85.3% | 86.9% |
  | Bakerloo | 90.6% | 91.5% |
  | Northern | 62.0% | 63.8% |
  | Victoria, Central | 85.8%, 85.4% | 85.8%, 85.7% |
  | Mildmay | 108% | 86.4% |
  | Windrush | 121% | 99.1% |
  | Elizabeth line | 111% | 100.5% |
  | Weaver | 120% | 108.9% |
  | Lioness | 200% | 129.8% |
  | Liberty | 195% | none (termini only) |

  Excess wait, long gaps and the Honesty Gap do not use this measure. They move only through D65: the Metropolitan's excess wait goes from 3.76 to 3.17 minutes, and every other line's by under 0.1 minutes.
- **Station views:** `fct_station_weekly_measured.trains_on_boards_share` is per station and still includes termini, where it reads as above (Uxbridge about 0, Stratford about 1.8). Station publishing is switched off (`STATIONS_PUBLISH_ENABLED`). Before it is switched on, termini there should be withheld or labelled. D65 also reaches the station views through `int_station_arrivals_directed`. On 17 Nov (from 12:38 UTC) it filled 133,576 of the line feed's 842,955 predictions with no direction (15.8%; 2.3% of all 5.78 million). The effect per station is not yet measured.
- **Alternatives:** keep termini and fill their directions (tried under D65, which over-counts at reversing termini); report termini separately.
- **Note:** past weeks are rewritten on the next run (D39), so W46's published figures will be restated.

### D1. Accept ADR-006 (poller reserved concurrency waits for the quota)

- **Context:** the poller runs unreserved because the account's concurrency quota is 10. The plan's reservation of 1 is the guarantee against overlap and runaway concurrency.
- This deviates from the plan and depends on a quota.
- **Default:** accept. Switch to `1` once the quota is granted.
- **Status 2026-11-15:** the only decision still open. ADR-006 stays proposed until the Lambda concurrency quota is granted; nothing published depends on it.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.

## Settled on 2026-11-15, with the detail

The owner accepted every recommendation below on 2026-11-15; each section ends with its outcome. The table under "Settled" summarises them.

### D2. Accept ADR-007 (reference Lambda 1024 MB / 300 s)

- **Context:** the plan's 512 MB / 60 s is too small for the weekly reference fetch.
- The change is already deployed. It costs pennies a month, but it is a cost change and a deviation from the plan.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-007 accepted: the reference Lambda stays at 1024 MB / 300 s.

### D3. Accept ADR-008 (staging merges whole days on a row key)

- **Context:** the plan says staging is "incremental append". Append isn't idempotent, so staging merges on a key instead.
- That sets the grain of every staging model, and the metrics are built on those models.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-008 accepted: staging merges whole days on a row key.

### D4. Accept ADR-009 (CI roles trust immutable OIDC subjects)

- **Context:** the trust policy uses the prefix `repo:KarshVashi@41749592/mind-the-data@1442302649`. It has been live since #16, so accepting the ADR changes nothing deployed.
- It is an IAM trust boundary.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-009 accepted: CI roles trust immutable OIDC subjects.

### D5. Station budget: 50 or about 55

- **Context:** `config/stations.yaml` uses all 50 distinct stations, at 52 calls a minute.
- About 55 adds branch ends for 57 calls a minute, still well under the key's 500. It means changing the loader's cap, the comment in `stations.yaml` and PLAN 5.2/5.3.
- **Default:** stay at 50. Revisit in the week-one review with real coverage numbers.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** Stays at 50 stations; revisit with coverage numbers.

### D6. sqlfluff strictness

- **Context:** `.sqlfluff` runs every rule with the jinja templater, a 100-character line limit and explicit aliasing. Any violation fails `make check`.
- The open question was whether to relax any rules.
- **Default:** keep the current config. Exclude a rule only when it trips, with the reason in a comment.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** sqlfluff config kept as it is.

### D7. Plan checkbox while the plan stays local

- **Context:** the plan is local-only by the owner's choice, so no PR can tick its checkboxes, which the workflow asks for.
- The local copy currently lags: M1 and the H-steps are still unticked.
- **Default:** tick the local plan when each PR opens, and track progress in this repo through STATUS.md. PR "Why" sections name the milestone and task by id ("M2, task 6").
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** As written: the local plan is ticked, progress is tracked in STATUS.md.

### D8. Rebuild prod staging after #17

- **Context:** `transform-daily` ran in prod before #17. Then, arrivals and stop disruptions parsed as null-filled rows. A merge never deletes rows (ADR-008), so null-keyed rows may still sit beside the corrected ones.
- A `--full-refresh` rewrites the prod staging tables from raw.
- **Default:** check prod for null-keyed rows once AWS access is back. If any are found, run a full refresh in a one-off workflow run.
- **Now possible in CI:** `transform-daily` takes `full_refresh` and `select` on a manual run (runbook, "Full refresh in prod"), e.g. `-f full_refresh=true -f select='staging+'`, as the transform role.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default` (data deletion).
- **Settled 2026-11-15 (owner):** Settled by the D45 full refresh of `stg_arrivals+`, which rewrites staging from raw (run on 2026-11-14).

### D9. dbt unit tests in prod builds

- **Context:** since #18, each daily prod `dbt build` creates and drops temporary Iceberg tables in the prod Glue database, one per unit test.
- The alternative is `--exclude-resource-type unit_test` in prod. CI has no warehouse access, so unit tests would then run only on manual dev builds. That gap is how the failures fixed in #18 went unnoticed.
- **Default:** keep running unit tests in prod until a dev unit-test workflow exists. That workflow needs an OIDC role, so it gets its own decision.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** Unit tests keep running in prod builds.

### D10. Pin dbt-athena to a minor version

- **Context:** `pyproject.toml` allows `dbt-athena-community>=1.9,<2`, and `uv.lock` resolves 1.11.1.
- The code lives in `dbt-athena`, also 1.11.1. #18 overrides its `resolve_table_type` macro and was checked against 1.11.1.
- **Default:** pin `dbt-athena-community>=1.11.1,<1.12`, and add `dbt-athena>=1.11.1,<1.12`. Upgrade in a deliberate PR that runs the dev unit tests.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** Done: `dbt-athena-community` and `dbt-athena` are pinned to `>=1.11.1,<1.12` in `pyproject.toml`, with `uv.lock` consistent.

### D11. ADR-010: bind OIDC roles to specific workflows

- **Context:** ADR-010 and its first step merged in #22 and were applied on 2026-11-08: the observer and publish roles, and the plan role on `main`, are bound to their workflow files. The `new-roles` trust probe passed the same day.
- The autonomy bundle (Part 4) sets `enforce_workflow_binding` to `true`, so the plan (pull request), apply and transform roles are bound too. It takes effect only with your `make bootstrap-apply`.
- **Default:** none. Accept ADR-010, merge the bundle, apply, then run the `all-roles` probe and the role checks (runbook H6, step 5).
- **Deadline:** 2026-11-11 12:00 UTC (72 hours after the bundle opened). **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-010 accepted. The Terraform change is already in `infra/bootstrap` (`enforce_workflow_binding` defaults to `true`). It reaches AWS with the owner's `make bootstrap-apply`, then the `all-roles` probe (runbook H6, step 5).

### D12. Smoke test after each deploy invokes the poller

- **Context:** the deploy smoke test (autonomy bundle, Part 2) invokes the poller once after `apply`. Each deploy leaves one minute with two polls 20 to 40 s apart, the same overlap ADR-006 already allows. Later `int_` models must treat a minute as one or more polls.
- The alternative is to wait for the next scheduled object instead of invoking. It is slower and doesn't prove the deployed code runs on demand.
- **Default:** none: this touches metric semantics. Accept the extra poll, or ask for the wait-only check.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted: the extra poll after each deploy's smoke test stays.

### D13. Ops checks when the observer role can't be assumed

- **Context:** `ops-daily` and `ops-daytime` (Part 3) skip with a warning, and stay green, when the role can't be assumed. If the observer trust broke later, the checks would go quiet.
- **Default:** fail the job when the role variable is set but the role can't be assumed. A two-line follow-up.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `auto-apply`.
- **Settled 2026-11-15 (owner):** Done: `ops-daily` and `ops-daytime` now fail, not skip green, when the observer role cannot be assumed.

### D14. Automerge allow-list

- **Context:** automerge (Part 3) is off until `AUTOMERGE_ENABLED` is `true`. Its allow-list is `transform/models/`, `tests/` and `docs/` (not `docs/adr/`). An auto-merged dbt model runs on `main` as the transform role, even after the binding in D11.
- The merge gate (Part 5, ADR-011) covers the same need with stricter checks: protected paths, the PR's own plan evidence and a pinned head. Automerge runs the gate's checks only in part, through its own allow-list.
- **Default:** none: it is a security boundary. The recommendation is to remove automerge in favour of the gate (a follow-up deleting `automerge.yml` and `ops/automerge.py`). The alternatives are to keep it off, or to turn it on with the list cut to `tests/` and `docs/`.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Done: automerge is removed (`automerge.yml`, `ops/automerge.py`, its tests and docs). The merge gate stays.

### D15. ADR-011: the engineer may merge low-risk pull requests through the gate

- **Context:** `make merge PR=<n>` merges only when the gate returns ALLOW. The gate runs `main`'s copy of `scripts/merge_gate.py`, `src/mind_the_data/ops/merge_gate.py` and `config/merge_policy.yaml`. Protected paths, IAM, deletes and any unlisted Terraform type always need you. The accepted risks are listed in ADR-011.
- If you accept it, the local working rule "never merge PRs" changes to allow `make merge` only. Until then nobody runs `make merge`; `make merge-check` is a dry run and changes nothing.
- **Default:** none. Accept, reject, or accept with `transform/**` protected too.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-011 accepted as written: `make merge` may merge low-risk PRs through the gate.

### M3 decisions (D16 to D24)

D19 and D22 were settled by the owner on 2026-11-14 and are under "Settled". These come from the M3 bundle (`feat/m3-line-minute-metrics`). D16 to D23 are metric definitions, each implemented behind a named seed or variable, so changing the answer is a seed edit and the next build (ADR-012 rebuilds every minute from the seeds). D23 and D24 also deviate from the plan, through ADR-013 and ADR-012. They entered on 2026-11-08, so the reminder date is 2026-11-11 15:00 UTC. All are `never-default`.

### D16. Service Closed (code 20) is weighted 0 and left out of the score

- **Context:** the plan has no weight for code 20. TfL sends it every night, roughly 01:15 to 04:15, and the week-one data has it on every line. With no seed row, `assert_line_status_codes_are_weighted` would fail every build.
- **Implemented:** `severity_weights.csv` code 20, weight 0, `is_service_closed = true`, `source = decision_d16`. A Service Closed minute inside scheduled hours (about 25 line-minutes a day, all at the edges of the hours seed) is counted in `service_closed_minutes` and left out of the numerator and denominator, like engineering works.
- **Alternatives:** count it as an in-service minute at weight 0 (it then reads as good service); weight it like Suspended (8), which would punish the edges of the hours seed; treat it as missing.
- **Other codes** TfL lists in `Line/Meta/Severity` (1 and 11 to 19) have not appeared and are left out on purpose, so the first one fails a test and comes back here.
- **Default:** none. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted: Service Closed weighs 0 and is left out of the score.

### D17. Scheduled hours: timetables for 13 lines, estimates for 7

- **Context:** M3 needs fixed hours per line and day type. TfL timetables (`stg_timetable`, recorded 2026-11-05) cover the 11 Tube lines, DLR and Trams: the seed uses the first and last departures at the measurement stations, rounded out to 5 minutes, including Night Tube on Friday and Saturday. TfL returns 404 for Elizabeth line and Overground timetables.
- **Implemented:** `line_operating_hours.csv`. Elizabeth line and Overground use 05:30 to 00:30 (Sunday 07:30 to 00:00), cut back for Liberty (22:10) and Suffragette (00:00) where boards showed earlier last trains. Rows carry `source = estimate_2026_11_08`. Waterloo & City has no weekend hours, as its timetable has none. Public holidays use the calendar day; Overground night services are not modelled.
- **Alternatives:** hours from published TfL first/last train pages for the 7 lines; hours inferred from arrival boards (noisy: boards show predictions from 03:00); a public-holiday seed.
- **Default:** none. Confirm or supply hours for the 7 estimated lines.
- **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted: the estimates for the 7 lines stand until TfL hours are supplied.

### D18. Incidents and recovery

- **Context:** PLAN 6.2 says an incident starts when a line leaves Good Service for an unplanned severity, ends when it returns, and gaps under 5 minutes merge. It doesn't say how missing minutes, closures or the end of service affect that, or what "recovery" measures.
- **Implemented** (`int_incidents`, var `incident_merge_gap_minutes: 5`):
  - an incident minute is a scored minute whose worst status is unplanned with a weight above 0;
  - every other observed minute is quiet, including minutes outside service hours, planned statuses and closures;
  - missing minutes are bridged: no data neither starts nor ends an incident;
  - incident minutes separated by fewer than 5 quiet minutes merge;
  - an incident belongs to the London date and week it starts in;
  - recovery = minutes from the first to the last incident minute of a closed incident; open incidents have no recovery yet and are left out of the median.
- **Also depends on it:** the Recovery Time table (D59) is built on this draft and is published labelled `provisional_d18`. D18 was accepted on 2026-11-15 as a first definition and the label stays (D59) until a full month of incidents has been reviewed.
- **Alternatives:** let a long poller gap end an incident; measure recovery to the first Good Service minute specifically; count incidents in every day they touch.
- **Default:** none. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted: the draft definition of an incident and of recovery.

### D20. Ranking ties and unranked lines

- **Context:** the plan doesn't say how ties are broken or whether provisional lines are ranked.
- **Implemented:** rank by Disruption Index ascending; ties go to the higher Good Service %; lines still equal share a rank. Lines with no scored minutes are unranked. Provisional line-weeks (completeness under `provisional_completeness: 0.9`) are ranked and flagged. Since D22 was settled (2026-11-14), a line that ran under half its scheduled minutes because of engineering works is also unranked.
- **Alternatives:** leave provisional lines unranked; break ties by fewer incidents.
- **Default:** none. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted (ADR-013 part).

### D21. Time bands follow the London calendar day

- **Context:** PLAN 7.1 says weekday peaks weigh 2, other weekday service 1, weekends and Night Tube 0.75.
- **Implemented:** the band comes from the London calendar date and time of the minute (`time_bands.csv`, `day_types.csv`). So Night Tube minutes, which all fall on Saturday and Sunday mornings, are 0.75, and the 00:00 to 00:30 tail of Friday's normal service counts as weekend. The tail of Sunday's service after midnight counts as Monday, weekday off-peak (1).
- **Alternative:** band by service day, so Friday's late-night tail stays weekday.
- **Default:** none. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted (ADR-013 part).

### D23. Which entries describe a minute

- **Context:** ADR-004 reads the latest successful poll in the minute. Polls per minute can be more than one (post-deploy smoke test, ADR-006). ADR-013 (proposed) amends ADR-004's rules 1 and 4.
- **Implemented:** the latest successful poll in the minute that lists the line. An entry counts if it has no validity period or one overlapping the minute. TfL sometimes keeps listing a planned status a minute after its period ends, with no other entry for the line; that minute is missing (`missing_reason = no_current_entry`, 1 to 5 line-minutes a day).
- **Alternatives:** read only the very latest poll, so a later poll that omits a line makes the minute missing; keep a just-expired entry as current.
- **Default:** none. Accept or reject ADR-013. **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** Accepted (ADR-013 part). ADR-013 is accepted.

### D24. Declared-view intermediate models rebuild in full

- **Context:** PLAN 6.1 lists intermediate models as incremental merges. A status run or an incident can start before any incremental window, and the minute model applies seeds that D16 to D22 expect to change. A windowed merge would split runs and leave older weeks on old definitions, silently.
- **Implemented:** `int_line_minute_status`, `int_status_intervals`, `int_incidents`, `dim_service_hours` and the marts are Iceberg tables rebuilt from `raw_start_date` each run. The M4 bundle adds `int_scheduled_headways` on the same terms: it applies the seeds of D33 and reads only the weekly timetables. Staging stays incremental. The cost is a few hundred MB scanned a year at most. See ADR-012 (proposed).
- **Default:** none: it deviates from the plan. Accept ADR-012, or ask for a seed-free incremental layer under a table that applies the seeds.
- **Class:** `never-default`.
- **Settled 2026-11-15 (owner):** ADR-012 accepted: the declared and measured models rebuild in full; revisit when a run scans about 5 GB.

### M4 decisions (D25 to D34, D40 to D45)

These come from the M4 work in the M4/M5 bundle. They are readings of PLAN 7.2 and 7.3 where the plan is ambiguous. Each is implemented as the most conservative reading available: missing data is never read as either good or bad service, and where the boards are ambiguous the error should not create a long gap that did not happen. Where a reading still carries a bias, the decision names it. They entered on 2026-11-08, so the reminder date is 2026-11-11 18:00 UTC. All are `never-default` (metric semantics). Several depend on the week-one data review (due from 2026-11-11). Those carry a recommendation to revisit once the review is in.

### D25. What counts as a duplicate prediction

- **Context:** PLAN 7.2 says to collapse duplicate predictions with different ids but "near-identical" `expected_arrival`, without a threshold.
- **Implemented** (`analysis/arrivals.py`, `duplicate_tolerance_seconds = 15`): within one board (station request, platform, direction) in one poll, predictions are ordered by expected arrival, then id. A prediction within 15 s of the row before it is dropped, and the earliest is kept. The comparison is with the previous row, not the previous kept row, so SQL can do it with `lag`.
- **Alternatives:** use the 45 s matching tolerance (two trains cannot share a platform 45 s apart, but that also merges real close pairs at platforms that serve two routes); keep the duplicate with the smallest `time_to_station`.
- **Recommendation:** keep 15 s until the review measures how far apart real duplicates sit.
- **Week-one review (2026-11-08):** keep 15 s and add a same-destination condition (Central close pairs are 93% two trains). **Code now:** 15 s, no destination condition, in both the reference and `int_inferred_arrivals`. It makes no difference on the Victoria line.
- **Settled 2026-11-15 (owner):** Accepted: the 15 s duplicate window. A same-destination condition (the review's ask) is a later refinement; no code now.

### D26. How sightings link, when a train has arrived, and the held-train bias

- **Context:** PLAN 7.2 rule 2 says a train has arrived when it is within 120 s in poll *t* and absent in *t+1*, "or shows `time_to_station` = 0". Rule 1 links sightings whose expected arrivals are within 45 s across consecutive polls. TfL's `expected_arrival` is its timestamp plus `time_to_station`, so a train that is not moving pushes its expected arrival on by about a minute every minute, and the literal 45 s rule cannot follow it.
- **Implemented** (`analysis/arrivals.py`):
  - Links are mutual nearest pairs between a station's consecutive successful polls. That keeps them one to one and computable in SQL without recursion.
  - A moving link is the plan's rule: expected arrivals within 45 s.
  - A standing link is an addition to the plan, for a train at the platform. It applies when the later sighting shows 0 s, the earlier at most 45 s, and the expected arrival moved by the poll gap give or take 45 s. It ranks after every moving link. Without it, a train standing at 0 s for two polls counted twice.
  - A train held short of the platform is not linked this way. Tried on real boards, a standing link at any time to station merged consecutive trains: at Oxford Circus the next train regularly shows, a poll later, the time to station the previous one had. Victoria inbound fell to 24 an hour at peak against 36 scheduled.
  - A train's last sighting is an arrival if the station's next successful poll exists and the sighting is at most 120 s out, or if no later poll exists and it shows 0 s. The arrival time is that sighting's expected arrival, the plan's rule 3. For a train that stood at the platform, that is the last poll it was seen there, up to its dwell after it arrived.
  - Two arrivals on one board within 45 s are one train that dropped off the board for a poll (flicker), and the later is kept.
- **Known bias:** a train held short of the platform for several polls counts once per poll while it is within 120 s. That puts short headways into exactly the delays the measured view exists to catch, and hides long gaps. It is a bias against showing delays, not a safe side.
- **Real data** (2026-11-05, Victoria line at Oxford Circus and King's Cross, both directions, against about 36 scheduled an hour at peak and 26 to 27 at midday):

  | Linking | Arrivals | Notes |
  | --- | --- | --- |
  | Plan-literal | 2,225 | |
  | As implemented | 2,147 | The flicker rule removes 78. King's Cross inbound shows 42 to 43 an hour at peak, which is 119%. |
  | By `vehicle_id` (D27) | 1,838 | About 30 to 32 an hour at peak and 22 to 24 at midday, 85% to 90% of the timetable. |

  The boards also give `expected_arrival` in 30 s steps from a timestamp about 40 s older than the poll.
- **Alternatives:**
  - link on order, since trains cannot overtake on one platform;
  - link held trains when vehicle ids agree only (D27);
  - cap the arrival time at the first 0 s sighting, which needs the whole chain and is not set-based.
- **Recommendation:** use vehicle-id linking on the Tube lines where the review finds ids stable. It removes the held-train bias. Settle the rest after the review measures expected-arrival drift in the last 120 s.
- **Week-one review (2026-11-08):** keep the mutual-nearest and standing links, link by vehicle id on enabled lines, and read time to go from the poll (D40). Keep the 45 s collapse and 60 s polling (ADR-002).
- **Code now (2026-11-09):** the review's recommendation, in the reference and in `int_inferred_arrivals`: vehicle-id linking on the lines `seed_vehicle_id_lines` enables (Victoria only, D27), time to go from the poll (D40), the 45 s rules unchanged. Pending the owner.
- **Settled 2026-11-15 (owner):** Accepted, with D27.

### D27. Which lines link sightings by vehicle id

- **Context:** PLAN 7.2 rule 1 uses `vehicle_id` "where it is stable", which is a week-one review question.
- **Implemented:** `InferenceParams.vehicle_id_lines` is empty, so every line uses the expected-arrival rule until the review names lines. On a listed line, two sightings with vehicle ids link only if the ids match and the expected arrival moved by no more than the poll gap plus 45 s. Without that bound, a placeholder id shared by several trains would chain them. Sightings without an id fall back to expected arrival. DLR boards send no vehicle id at all.
- **Alternatives:** trust vehicle ids on all Tube lines now, since the D26 figures suggest they are stable on the Victoria line.
- **Recommendation:** fill the list from the review, line by line.
- **Week-one review (2026-11-08):** Victoria now; Northern, Jubilee, Piccadilly, Central, Bakerloo, District, Hammersmith & City, Circle and Metropolitan after the Monday 9 November check; Elizabeth and Overground (trip ids) can switch on; not DLR, Waterloo & City or tram.
- **Code now (2026-11-09):** `seed_vehicle_id_lines` enables **Victoria only**, every row `decision_status = pending_owner_d26_d27`, with the review's advice per line in `review_recommendation`. To change a line, flip `link_by_vehicle_id` and rebuild. Placeholder ids `000` and `001` count as no id (var `placeholder_vehicle_ids`, and `InferenceParams.placeholder_vehicle_ids` in the reference).
- **Recheck (2026-11-13, [gate recheck](data-quality/2026-11-12-gate-recheck.md)):** ids are stable on Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern and Piccadilly; District is not (78.5% followed from Victoria to Earl's Court). Under the ADR-014 gate (D46) the unlinked lines' failing hours are almost all above 120%, the held-train double count linking removes; Piccadilly (74.1%) and Metropolitan (75.5%) are the two lines under 80%. **Recommendation:** link the eight lines. Still Victoria only until the owner answers.
- **Settled 2026-11-15 (owner):** Accepted: the eight lines (Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern, Piccadilly) link by vehicle id with Victoria; District stays out. `seed_vehicle_id_lines` is updated. Circle and Hammersmith & City stay withheld by D52.

### D28. A board is a platform, and absence needs a successful poll

- **Context:** the plan says "for each platform and direction" and "absent in poll *t+1*", but not what a failed poll means.
- **Implemented:**
  - A board is (station request, NaPTAN id, line, platform name, direction). A null direction is its own board, which is common on Overground boards.
  - "Next poll" is the station's next successful arrivals poll. A failed poll neither confirms nor denies an arrival.
  - Each arrival carries `confirmed_at`, so a headway that spans a collection gap can be dropped in `int_headways` rather than read as a long gap.
  - After a lost poll, a train last seen 45 to 120 s out and next seen standing at the platform has two sightings neither link joins. It counts twice unless the flicker rule merges them.
  - A train that moves platform mid-approach ends on the first platform. If it is within 120 s there, it counts as an arrival there too: a double count.
  - Arrivals at the end of the input are provisional (a 0 s sighting at the last poll can link onward later). So an incremental `int_inferred_arrivals` must read at least one poll past the window it writes, and the oracle comparison must give both sides the same input.
- **Alternatives:** follow trains per station and direction, across platforms.
- **Recommendation:** keep it, and count platform changes in the review.
- **Week-one review (2026-11-08):** keep the platform as the board at core stations (2% change platform), but pool platforms by vehicle id for Metropolitan and District (14% and 6.5%). **Code now:** platform boards on every line; the pooling is not built, since neither line links by vehicle id yet. Headways pool platforms per station and direction (D29).
- **Settled 2026-11-15 (owner):** Accepted.

### D29. Scheduled service is per station and direction, not per platform

- **Context:** PLAN 7.3 compares headways "at a platform and direction" with `int_scheduled_headways` "for that line, station, direction". TfL timetables have no platforms. At Earl's Court, the District inbound timetable lists two departures in the same minute 21 times on weekdays, one per eastbound platform.
- **Implemented:** `int_scheduled_headways` is per station and direction, pooling every timetable route.
- **Consequence for `int_headways` (not built yet):** pool a station-direction's platforms before forming headways. Per-platform headways at a two-platform station would read as about twice the scheduled ones, and become false long gaps.
- **Alternatives:** split scheduled departures across platforms using board history.
- **Recommendation:** pool, as implemented.
- **Settled 2026-11-15 (owner):** Accepted.

### D30. Which hour a gap belongs to, and service-day hours

- **Context:** PLAN 7.3 uses headways "in the hour" without saying which end of a gap sets the hour.
- **Implemented:**
  - A gap belongs to the hour of the departure that ends it, which is the train a waiting passenger boards. `int_headways` must use the same rule.
  - Hours are TfL's service-day clock (0 to 27). Hour 24 and later are the next calendar morning (`london_hour`, `is_next_calendar_day`).
  - Gaps are not formed across schedules: Friday's last Night Tube train and Saturday's first are in different schedules.
  - The sums of gaps and squared gaps are kept, so `int_headways` can add Friday hour 27 and Saturday hour 3, which fall in the same London hour.
- **Alternatives:** split a gap across the hours it spans; give it to the hour it starts in.
- **Recommendation:** keep it.
- **Settled 2026-11-15 (owner):** Accepted.

### D31. Two departures in one minute both count

- **Context:** TfL gives departure times to the minute. In the 2026-11-05 fetch, 127 journeys share their minute with another journey of the same route at the same station and direction, across all schedules. They are on the Jubilee, District, Victoria and Waterloo & City lines and the tram.
- At DLR Canning Town, each departure of route 1 is also listed in route 2 at the same minute (120 inbound on weekdays). That could be one train listed twice, or the Stratford International and Bank services leaving their two platforms together.
- **Implemented:** every listed journey counts, entered as a 0-minute gap. A 0 gap adds nothing to the sums in the average-wait formula, but it adds a scheduled train.
- If the Canning Town pairs are duplicates, scheduled trains there are overstated by about half. The quality gate would then exclude those hours rather than score them.
- **Alternatives:** collapse same-minute departures listed under different routes.
- **Recommendation:** decide Canning Town from its boards in the review. DLR boards there carry no vehicle ids and few predictions (see open questions in STATUS.md).
- **Settled 2026-11-15 (owner):** Accepted.

### D32. Timetable versions

- **Context:** the plan doesn't say which weekly timetable applies to which days.
- **Implemented:** the latest fetch of each UTC day is a version, valid from that date until the next version's date. The first version also covers the days collected before it (data starts 2026-11-01, the first fetch was 2026-11-05).
- If a later fetch fails, the last good version stays in force; `timetable_fetched_at` shows its age.
- **Alternatives:** use only the latest timetable for all history (a December timetable change would then rewrite November); treat a failed week as no timetable (unmeasured).
- **Recommendation:** keep it.
- **Settled 2026-11-15 (owner):** Accepted.

### D33. Fallback frequencies for the Elizabeth line and the Overground

- **Context:** TfL returns 404 for every Elizabeth line and Overground timetable request: 46 station-directions across 7 lines, the same set as D17.
- **Implemented:** `seed_line_frequencies` holds per-station trains per hour for those 23 stations, 33 station-directions in all. A terminus has only the direction its board labels trains with (for example Watford Junction `inbound` and Euston `outbound`); the others have `both`. Barking Riverside's direction is assumed, since its board showed no Suffragette trains during the closure. There are weekday peak (07:00 to 10:00, 16:00 to 19:00) and off-peak values, and Saturday and Sunday values, all `source = estimate_2026_11_08` and flagged `is_fallback`.
  - Values come from published service patterns. At mid-line stations they were checked against inferred arrivals on 2026-11-05 (vehicle-id linking). For example, Tottenham Court Road showed about 24 an hour per direction at peak and about 16 off-peak; Romford about 12 and 8; Canada Water about 16.
  - Where the two sources disagreed, the lower figure was used. A lower scheduled frequency means a longer scheduled headway, so service is never judged against a timetable busier than the real one.
  - Terminus boards over-count badly (for example, Watford Junction showed 41 at 21:00 against 3 an hour), so they were not used.
  - Only whole hours inside `line_operating_hours` are covered. A station-direction takes the fallback only if no fetch ever returned a timetable for it, so a direction TfL starts serving moves to its timetable.
- **Not covered by the fallback:**
  - Tube termini in the direction TfL returns 404 for. Trains end there, so there is nothing to wait for.
  - Tram at New Addington (both directions) and East Croydon inbound, which also return 404 while the rest of the tram timetable works. They stay unscheduled and unmeasured.
- **Caveat:** the fallback lines' quality gate compares boards with estimates partly checked against the same boards. A pass for these lines is weak evidence.
- **Alternatives:** take frequencies from TfL's published working timetables; leave these lines out of the measured view until real timetables exist; add tram rows.
- **Recommendation:** confirm or replace the 23 stations' values (owner). Report the fallback lines separately in the M4 acceptance figure.
- **Settled 2026-11-15 (owner):** Accepted.

### D34. Scheduled headway for long gaps when gaps are uneven

- **Context:** PLAN 7.3 counts a long gap when a headway exceeds 2 x the scheduled headway, but the scheduled headway is ambiguous when an hour's gaps are uneven. On weekdays at 12:00, Victoria at Oxford Circus has a mean of 134 s and a longest gap of 180 s.
- **Implemented here:** `int_scheduled_headways` exposes both `mean_headway_seconds` and `max_headway_seconds`; `int_headways` will choose.
- **Recommendation for `int_headways`:** use the longest scheduled gap in the hour. Then a long gap is one that no scheduled gap that hour comes near, which is the conservative reading for the Honesty Gap headline.
- **Alternative:** the mean, which flags more gaps.
- **Settled 2026-11-15 (owner):** Accepted.

### M5 decisions (D35 to D39)

These come from the M5 publish work in the M4/M5 bundle. D35 to D38 entered on 2026-11-08, so the reminder date is 2026-11-11 22:00 UTC; D39 entered on 2026-11-09, reminder 2026-11-12 22:00 UTC. All are `never-default`. D35, D37 and D38 are listed in every published file's `open_decisions`. D36 was settled by the owner on 2026-11-14 and is under "Settled".

### D35. Mark lines scored on a small share of their hours as provisional

- **Context:** a line with long closures is scored only on the minutes left, and delays on top of a part closure stay in while the Good Service around them drops out. For 4 to 8 November, Suffragette had 94% of its scheduled minutes under closures and was ranked last on 5 scored hours; Tram had 42% and Mildmay 36%.
- **Implemented:** display only. A line with at least 25% of its scheduled minutes under closures (`CLOSURE_SHARE_PROVISIONAL` in `src/mind_the_data/publish/league.py`) gets a provisional marker on the cards, in the JSON (`provisional_reasons`, code `mostly_engineering_works`, decision `D35`) and in the numbers sheet. Its rank is unchanged (D20). A provisional line is never named in the insight line (D38), so this threshold also decides which line the headline can name.
- **With D22 settled (2026-11-14):** the two rules nest and do not contradict each other. Under 25% closed: ranked, no marker. 25% to 50%: ranked and provisional (this decision). Over 50%: not ranked, shown as "Not ranked: engineering works" (D22); the card does not add the provisional mark to such a row, since its own note already says why. Suffragette (94%) is now unranked; Tram (42%) and Mildmay (36%) stay ranked and marked. D35 is kept as it was; only its `decisions` list drops `D22`, which is no longer open.
- **Alternatives:** a different threshold; drop the marker now that D22 unranks the worst cases.
- **Default:** none. **Class:** `never-default` (metric semantics and publishing).
- **Settled 2026-11-15 (owner):** Accepted.

### D37. Typical bad hours on each line page

- **Context:** PLAN 8 names "typical bad hours" in `lines/{line_id}.json` without defining them.
- **Implemented:** the London hours of day with the highest share of scored minutes that carried a weight above 0, over the line's whole history, unweighted by time band; at most three, worst first, ties to the earlier hour. An hour needs at least 300 scored minutes (`MIN_BAD_HOUR_SCORED_MINUTES` in `documents.py`), so one disrupted quarter-hour on a mostly closed line is not called a pattern. Planned Reduced Service counts as disrupted (D22).
- **Alternatives:** weight by time band or severity; count only unplanned statuses; a rolling window (the last 12 weeks) instead of the whole history, which also bounds the scan as data grows.
- **Default:** none. **Class:** `never-default` (metric semantics).
- **Settled 2026-11-15 (owner):** Accepted.

### D38. The share card's insight line

- **Context:** M5 fills the insight line from the week's biggest Honesty Gap, which needs M4.
- **Implemented:** the most Honesty Gap hours when any published line has some, worded "showed Good Service for N hours in which its trains ran with long gaps"; otherwise, since the measured view is published (D42), the lowest trains-run share under 95%, worded "Only X% of the line's timetabled trains showed on TfL's arrival boards"; otherwise the lowest Good Service % among lines that are not provisional, worded "showed Good Service for only X% of its scored service time" (scored time leaves out closures, Service Closed and missing minutes). A provisional line is never named, and a weak-evidence line (D53) is never named for a measured headline. With nothing to name, the card shows a neutral line.
- **Alternatives:** the highest Disruption Index; the longest incident; write the insight by hand for each post.
- **Default:** none. **Class:** `never-default` (publishing and metric wording).
- **Settled 2026-11-15 (owner):** Accepted; D54 overrides it for the Honesty Gap.

### D39. Rewrite every archived week on each publish

- **Context:** the website needs an archive index (`meta/index.json`, see `docs/site-integration.md`). `lines/{line_id}.json` is already rebuilt from the current marts on every run, so a league file written once and never touched again would drift from the line pages after any metric change or backfill.
- **Implemented:** each export run writes `league/{iso_week}.json` for every complete week with observed data, `league/latest.json` for the newest complete week (whichever week was asked for), and `meta/index.json` listing exactly those weeks and the line files. Past weeks therefore always show the current methodology; `generated_at` and `git_sha` say which. Share cards are still rendered only for the week asked for, so a card is a record of what was posted.
- **Alternatives:** write each week once and freeze it (the archive then shows the methodology of its day, and the index must be built from an R2 listing at upload time); freeze weeks only after D16 to D23 are settled.
- **Default:** none. **Class:** `never-default` (publishing).
- **Settled 2026-11-15 (owner):** Accepted: every archived week is rewritten on each publish.

### M4 decisions, continued (D40 to D45)

These came out of the week-one review and the measured-view review, after D35 to D39 had been taken by the M5 work. They entered on 2026-11-09, so the reminder date is 2026-11-12 18:00 UTC. All are `never-default`. D42 and D44 were settled by the owner on 2026-11-14 and are under "Settled".

### D40. Time to go is read from the poll, not TfL's `time_to_station`

- **Context:** the week-one review (Q4) found `time_to_station` counts from the prediction's own timestamp, about 40 s before the poll, and repeats between polls. A train whose last sighting shows 121 s can be 16 s from the platform, so the 120 s arrival rule dropped it.
- **Implemented:** every time-to-go rule (the 120 s threshold, "shows 0", the standing link) reads `expected_arrival - polled_at` in whole seconds, truncated as Athena's `date_diff` does and floored at 0. TfL's figure stays on the output as `last_time_to_station`. On the Victoria check this is the review's variant C (+2.7 points over variant B).
- **Alternatives:** keep `time_to_station` and raise the threshold to 180 s (variant B gains 2.5 points).
- **Class:** `never-default` (metric semantics). **Recommendation:** keep.
- **Settled 2026-11-15 (owner):** Accepted.

### D41. Measured-view models rebuild in full

- **Context:** PLAN 6.1 makes intermediate models incremental merges. An inferred arrival depends only on its poll and the station's next one, but an arrival at the end of the data is provisional, and a merge cannot remove one that a later poll turns into a moving train.
- **Implemented:** `int_inferred_arrivals`, `int_headways` and `int_measured_hours` are tables rebuilt each run, as D24 did for the declared view (ADR-012, proposed). On dev, `int_inferred_arrivals` builds in about 20 s from five days of staging.
- **Cost:** the build reads ten columns of `stg_arrivals` over all history, about 1.4 million rows a day; a year is about 0.5 billion rows. Once the scan passes about 5 GB a run, switch to an incremental merge that rewrites whole days and trims the last 30 minutes of each run.
- **Class:** `never-default` (deviates from the plan). **Recommendation:** accept for now, revisit in M7.
- **Settled 2026-11-15 (owner):** Accepted with D24 (ADR-012).

### D43. Lines that are not measured

- **Context:** the week-one review found four lines that cannot be measured as things stand.
- **Implemented:** `seed_measured_lines` sets `is_measured = false` for the DLR (no vehicle ids, minute-quantised estimates, 30% to 135% of the timetable), the tram (closures and unscheduled directions), Waterloo & City (both stations are termini) and the Suffragette line (closed at both stations during the review). `fct_line_weekly` shows them as `not_measured` with no measured numbers or rank. Every row is `pending_owner`; flip the flag and rebuild to change one.
- **Alternatives:** measure the DLR at Canning Town only; keep the tram and Suffragette in once their closures end (after 10 November).
- **Class:** `never-default` (metric semantics, publishing). **Recommendation:** keep until the review is re-run on fixed staging and after the closures.
- **Settled 2026-11-15 (owner):** Accepted; revisit after the closures.

### D45. `stg_arrivals` keys on the prediction, and prod needs a full refresh

- **Context:** the week-one review (Q5) found `stg_arrivals` keyed on `(poll_id, request_path, prediction_id)`. TfL's id is not unique on a board: every DLR prediction at a station shares one id, and a train that terminates and leaves again keeps its id on two platforms. On 6 November staging dropped 83.7% of DLR rows and up to 38% at Tube termini. This is a bug fix to merged M2 staging.
- **Implemented:** the key adds line, stop, platform, direction, vehicle, destination and expected arrival. On dev, 6 November goes from 1,250,353 to 1,444,004 rows against 1,444,816 raw (the 812 left are the same prediction listed twice). `assert_staged_arrivals_match_raw` fails when staged rows differ from the distinct raw predictions of any line-day, either way: fewer means a key narrower than a prediction, more means rows left by an older key. TfL does list one prediction twice (8% of Piccadilly rows overnight), so raw rows are not compared one for one.
- **Prod:** the merge key changed, so an incremental run would keep the old rows beside the new ones for the days it rereads, and older days stay short. Prod needs `dbt build --full-refresh --select stg_arrivals+` once after merge (owner step in STATUS.md). The dev table was rebuilt this way. Since M7 this runs in CI: `gh workflow run transform-daily.yml --ref main -f full_refresh=true -f select='stg_arrivals+'`.
- **Class:** `never-default` (data rewrite). **Recommendation:** accept, and run the full refresh straight after merge.
- **Settled 2026-11-15 (owner):** Done: the new key was accepted, the prod full refresh of `stg_arrivals+` ran on 2026-11-14, and `transform-daily` has passed since, including 493 of 493 on 2026-11-15.

### Measured-view publishing decisions (D51 to D54)

These come from publishing the measured view after the owner settled D42 on 2026-11-14 (`feat/collate-a-measured`). D54 came from the owner's review of the bundle the same day. They entered on 2026-11-14, so the reminder date is 2026-11-17 18:00 UTC. All are `never-default` (metric semantics, publishing) and are listed in every published file's `open_decisions`. D42, D44, D46 and D47 to D50 were settled by the owner the same day and are under "Settled".

### D51. A line-week's own gate decides whether its measured numbers are published

- **Context:** D42 publishes the measured view "once the gate passes". The M4 gate is one figure over all core-station Tube hours; it does not say whether a given line's week can be trusted. On 9 to 14 November the Tube lines' core hours pass at 75% to 98%, the Lioness line's at 42%.
- **Implemented** (`fct_line_measured_daily`, `fct_line_weekly_measured.measured_withheld_reason`, mirrored in `publish/league.py`): the build-wide limit, applied to each line-week. Its judged hours at core and branch stations (termini over-count, D33) must have at most `gate_max_out_of_band_share` (30%) out of band, and at least `min_measured_share` (50%) of its scheduled station-direction-hours must be measured (D42's existing rule). Otherwise the line-week's measured fields are `null` with a reason: `no_measured_hours`, `gate_not_judged` (no core or branch station, the Liberty line), `gate_failed` or `too_few_measured_hours`. A custom window sums the daily figures and applies the same rules. Experienced Reliability ranks only published line-weeks.
- **Result, 9 to 14 November:** published for Bakerloo, Central, District, Jubilee, Metropolitan, Piccadilly, Victoria, the Elizabeth line and Weaver; withheld for Lioness (`gate_failed`), Mildmay, Northern and Windrush (`too_few_measured_hours`) and Liberty (`gate_not_judged`).
- **Alternatives:** the M4 acceptance's 80% per line (Piccadilly and Metropolitan, at 76% and 75%, would be withheld); judge the line on all its stations, termini included (Central 70%, Northern 57%); no per-line gate.
- **Class:** `never-default`. **Recommendation:** accept.
- **Settled 2026-11-15 (owner):** Accepted: each line-week's own gate decides.

### D52. Circle and Hammersmith & City: the whole measured view is withheld until the split

- **Context:** ADR-014 withheld both lines' trains-run share because the boards on the shared section file every westbound train under Hammersmith & City. The same misfiling halves Hammersmith & City's westbound headways there (its excess wait reads low and its long gaps disappear) and leaves the Circle with no westbound trains at three of its four stations.
- **Implemented:** var `measured_view_withheld_lines: ["circle", "hammersmith-city"]` (`MEASURED_VIEW_WITHHELD_LINES` in `publish/league.py`); both lines show `measured_withheld_reason: shared_section_misfiled`. `trains_run_withheld_lines` stays, so if the view is ever shown before the split, trains run stays withheld. The split by vehicle-id history (ADR-014, decision 4) is not built: it needs cross-station state in both the Python reference and `int_inferred_arrivals`, with new parity fixtures from the shared section.
- **Alternatives:** publish the Circle, whose misfiled hours have no trains on the boards and drop out of the gate, so its numbers rest on eastbound and Victoria only; publish both with a flag.
- **Class:** `never-default`. **Recommendation:** accept until the split lands.
- **Settled 2026-11-15 (owner):** Accepted: Circle and Hammersmith & City stay withheld until the vehicle-id split.

### D53. Weak-evidence lines are shown but not ranked on Experienced Reliability

- **Context:** the Elizabeth line and the Overground are measured against fallback frequencies (D33), set to the lower of two estimates, so their scheduled waits are long and their excess wait reads low. On 2 to 8 November the Elizabeth line would have ranked first; its trains-run share was 1.20, more trains on the boards than the estimate.
- **Implemented:** a line-week with any measured hour on fallback frequencies (today only the Elizabeth line and the Overground; a Tube station-direction TfL stopped publishing a timetable for would trigger it too) has `is_weak_evidence` (`fct_line_weekly_measured`, and in the published `measured` object). Its numbers are published when its gate passes (D51), but it gets no `experienced_rank`, and the card's insight line never names it for a measured headline (D38).
- **Alternatives:** rank them with the flag; withhold them until real timetables exist.
- **Class:** `never-default`. **Recommendation:** accept.
- **Settled 2026-11-15 (owner):** Accepted: weak-evidence lines are shown, not ranked.

### D54. The share card's insight line leaves out the Honesty Gap for now

- **Context:** D38 put the most Honesty Gap hours first on the card once the measured view was published. ADR-014's open item 2 is unanswered: if the boards leave out trains that ran, hours pass the gate with gaps that exist only on the boards, and those long gaps feed the Honesty Gap. A card is the most shared form of the numbers and carries no caveat.
- **Implemented** (owner-directed default, `publish/insight.py`): `HONESTY_GAP_HEADLINE = False`. The insight is the lowest trains run against the timetable on a line that is neither provisional nor weak evidence, when under 95% (unchanged from D38), then the declared fallback, the lowest Good Service % among ranked lines. Neither Honesty Gap hours nor long-gap counts reach the card. The Honesty Gap stays in the league and line files, with its caveat beside it (`docs/site-integration.md`). Setting the constant to `True` restores D38's order; `pick_insight(..., honesty_gap_headline=True)` is tested.
- **Result (dev build, 14 November):** on 4 to 10 November the card reads "Only 68% of the District line's timetabled trains showed on TfL's arrival boards.", and on 9 to 13 November "Only 67% of ...". Under D38's order both would have led with a Honesty Gap count (on 9 to 13 November, Piccadilly: Good Service for 78 hours with long gaps, out of 86).
- **Alternatives:** keep D38's order with a caveat on the card (no room on 1200x675 without crowding); drop the measured view from the card entirely.
- **Class:** `never-default` (publishing). **Recommendation:** keep it off until open item 2 is answered (compare the boards with TfL's own trains-operated figures for a week), then switch it on.
- **Settled 2026-11-15 (owner):** Accepted: the Honesty Gap stays off the card. The trains-run insight now carries the caveat "Boards can miss trains that ran." On the 1200x675 card it falls back to the Good Service % insight if the footer does not fit (it fits today).

### M6 league mechanics (D56 to D61)

These come from the rest of M6: divisions, the season table, the Recovery Time table, the strikes-excluded league and the sensitivity analysis. They entered on 2026-11-14, so the reminder date is 2026-11-17 21:00 UTC. All are `never-default`, and all are listed in every published file's `open_decisions`.

### D56. Accept ADR-015 (seed the divisions from two weeks)

- **Context:** PLAN M6 seeds the divisions from the first four weeks. Four complete weeks end on 6 December, so the divisions would start in January 2027. The owner asked for shorter time-based requirements on 2026-11-14.
- **Implemented ([ADR-015](adr/015-divisions-seed-from-two-weeks.md), `rpt_division_seeding`):**
  - seeded from the first `division_seeding_weeks: 2` complete weeks (W46 and W47);
  - labelled `provisional` until `division_full_seeding_weeks: 4` complete weeks exist;
  - before the seeding weeks exist, the output is an explicit `not_seeded` state with no table;
  - the divisions play from December 2026.
- **Alternatives:** four weeks, as the plan (divisions from January 2027); re-seed automatically when four weeks exist.
- **Class:** `never-default` (deviates from the plan).
- **Settled 2026-11-15 (owner):** ADR-015 accepted: divisions seed from two complete weeks, labelled provisional until four exist.

### D57. How divisions are formed and how lines move

- **Context:** PLAN M6 says "monthly promotion and relegation of three lines". It does not give the number of divisions, the monthly measure, or what happens to lines that cannot be ranked.
- **Implemented (`rpt_divisions_monthly`):**
  - two divisions: the ten best seeded lines in Division One, the other ten in Division Two (`division_one_size: 10`);
  - each month is ranked within its division by the Disruption Index over the month's London dates, with the weekly table's rules (D19, D20, D22);
  - at the month's end, the bottom three ranked lines of Division One swap with the top three ranked lines of Division Two (`division_moves: 3`; ties split by line id);
  - a line not ranked for the month (mostly closed for works, or nothing scored) neither goes up nor down. If a division has fewer ranked lines than places, fewer lines move, so both divisions keep ten lines;
  - while a month runs, `movement` shows where each line stands; it becomes a result once the month is complete;
  - lines level on index and Good Service % at a promotion or relegation place are split by line id (alphabetical);
  - a month in which any ranked line was observed under 90% (`is_provisional`) moves no one (settled 2026-11-15): ranks show, every line stays, and `movement_held_reason` is `provisional_month`. The whole month is held so the swap stays balanced;
  - a line added to the lines seed after seeding never joins a division until the divisions are re-seeded;
  - if a division month has not been built yet, its file says `pending`, never `seeded`;
  - `assert_divisions_keep_their_size` checks every month's Division One holds ten lines, this month and next.
- **Known limit:** the months are chained at compile time from `raw_start_date`, so the query grows with every month (quadratically in planning work, since each month's CTE builds on the last). It is small for the first seasons. Before it nears Athena's limits, store each finished month's membership in an incremental table and chain from that.
- **Alternatives:** three divisions (Tube, rail, low volume); rank months on the season measure; let unranked lines be relegated; split ties by the previous month's rank.
- **Class:** `never-default` (metric semantics, publishing).
- **Settled 2026-11-15 (owner):** Accepted: two divisions of ten, three up and three down, unranked lines stay put. A provisional month (a ranked line under 90% observed) moves no one; `rpt_divisions_monthly` holds the whole month and sets `movement_held_reason = provisional_month`.

### D58. The season table

- **Context:** PLAN 6.2 gives `rpt_season` the grain "line x season (April to March)", and M6 says it resets each April.
- **Implemented (`rpt_season`):**
  - seasons run from 1 April to 31 March in London dates, labelled like `2026-27`;
  - each line's standing is the declared view over every date of the season so far, with the weekly table's rules (D19, D20, D22);
  - dates before the collector's first observed minute (4 November 2026) are left out, so until April 2027 the table is the season to date;
  - `season/{season}.json` is published for every season with data.
- **Alternatives:** the calendar year; rank on points from weekly positions rather than on the pooled index.
- **Class:** `never-default` (metric semantics, publishing).
- **Settled 2026-11-15 (owner):** Accepted: the season runs April to March on the pooled index.

### D59. The Recovery Time table rests on D18, which is open

- **Context:** PLAN M6 asks for a Recovery Time table. D18 (what an incident is and what recovery measures) is not settled.
- **Implemented (`rpt_recovery_time`, and `recovery_time` in each league file):**
  - built on D18's current draft: recovery is the minutes from an incident's first to its last incident minute; open incidents are left out;
  - lines are ranked by median recovery, quickest first; equal medians go to fewer incidents and still-equal lines share a rank;
  - a line with no closed incident is not ranked (`no_recovered_incidents`);
  - every row and the published block say `definition_status: provisional_d18`.
- D18 itself is unchanged and still open. Settling it may change every number in this table.
- **Alternatives:** rank by mean or longest recovery; publish nothing until D18 is settled.
- **Class:** `never-default` (metric semantics, publishing).
- **Settled 2026-11-15 (owner):** Accepted: the Recovery Time table publishes with its provisional label.

### D60. Industrial action seed and the strikes-excluded league

- **Context:** PLAN 7.4 keeps strikes in the league, tags weeks from an industrial action seed, and exports a "strikes excluded" variant. Neither the seed's shape nor the exclusion rule is specified.
- **Implemented:**
  - `seed_industrial_action` lists 2026 action on the lines in scope, one row per action, each with its public source (publisher, title, date, URL):
    - the RMT Tube drivers' strikes: March and May (called off), April and 2 and 4 June (held), and 26 to 27 November, 17 to 18 December and 19 to 20 December (announced);
    - three Windrush line contractor strikes in February to April. The February one had no service impact; the outcome of the other two is unknown.
  - `rpt_league_strikes_excluded` drops, per line, the whole London dates of `held` or `announced` action that applies to it (a noon-to-noon strike drops both days), then sums and ranks the rest of the week with the weekly rules.
  - A week with no such date is the main league exactly. `assert_strikes_excluded_matches_league_without_strikes` checks this on every build, and a unit test checks it on the recorded week.
  - Each league file lists the action touching its window (`industrial_action`) and the variant (`strikes_excluded`, with `is_same_as_main`).
  - An `announced` row must be updated to `held` or `called_off` once its date passes. `assert_announced_industrial_action_is_current` warns three days after an announced action's last day if it has not been updated, and the daily ops report lists the row as a problem (owner-directed: a missed hand update must never stop the nightly build; runbook, "Industrial action seed").
  - `rmt-tube-2026-04-23` is marked `held` on the TfL announcement and later reports that drivers struck twice in April; no report from the day was found. It is before collection began, so it changes no published number.
- **Not included:** the Evening Standard's report that 16 and 18 June were replaced by 2 and 4 June (no primary source found). The 2027 dates were added on 2026-11-15 (see Settled).
- **Alternatives:** drop only the strike hours (noon to noon), not whole days; exclude only the lines that ran no service, from TfL's status that day.
- **Class:** `never-default` (metric semantics, publishing).
- **Settled 2026-11-15 (owner):** Accepted. The 26 and 27 November and December rows are confirmed as `announced`, and the five January and February 2027 rows are added (ITV, 6 November 2026, corroborated by Time Out and ianVisits).

### D61. Sensitivity weight sets

- **Context:** PLAN 7.5 asks for weekly ranks to be re-run under "three alternative weight sets" with Spearman correlation and top and bottom three changes. The sets are not given.
- **Implemented (`sensitivity_weight_sets` seed, `rpt_weekly_status_mix`, `publish/sensitivity.py`):**
  - the three sets:
    - `flat_time`: every minute weighs 1;
    - `linear_severity`: severity weights 1 to 5 by order (Minor Delays, Special Service 1; Reduced Service 2; Severe Delays 3; Part Suspended and unplanned Bus Service 4; Suspended 5);
    - `peak_heavy`: peaks 3, weekends 0.5.
  - Each published week's ranked lines are re-ranked under each set from the same minutes.
  - `meta/methodology.json` gets, per week and set: Spearman's rho (ties averaged), the largest rank shift, and whether the top three or bottom three change.
  - A set re-weights the status ADR-004 chose under the base weights; it does not re-pick the worst status.
  - A week is skipped (no results) if any ranked line has no status mix rows, so a stale mart never re-scores a line as perfect.
  - Top and bottom three are compared as sets of lines, ordered with the line id as the last tie-break, so a tie at third place that only reorders can show as a change.
  - On W45 (4 to 8 November, about 70% observed, so not a complete week): rho 0.98 to 0.99, at most two places moved, top three unchanged under all three sets.
- **Alternatives:** other sets (for example severity squared, or no weekend discount); also re-pick the worst status under each set.
- **Class:** `never-default` (metric semantics, publishing).
- **Settled 2026-11-15 (owner):** Accepted: the three sensitivity weight sets.

### M7 decisions (D63, D64)

These come from the M7 hardening work (`feat/collate-c-m7`). D62 was not used. They entered on 2026-11-14, so the reminder date is 2026-11-17 18:00 UTC.

### D63. The open dataset's contents and licence

- **Context:** PLAN 8 lists `open-data/{yyyy-mm}/*.parquet` (M7) without contents or a licence. Republishing derived TfL data needs the attribution "Powered by TfL Open Data", and the project's own figures need a licence of their own.
- **Implemented** (`publish/open_data.py`, runbook "Monthly open data"):
  - one London calendar month per folder, exported on the 2nd;
  - `line_minute_status` (every line-minute, missing minutes flagged, TfL's reason text included), `line_daily` (`fct_line_daily`) and `line_weekly` (the declared-view columns of `fct_line_weekly` for the weeks that end in the month);
  - the measured view left out: it was built before D42 was settled, and adding `fct_line_measured_daily` to the monthly files is a follow-up that needs its own column list and caveat in the README;
  - a README and `manifest.json` in each folder, with the TfL attribution and terms link, and the derived figures offered under **CC BY 4.0**.
- **Not yet live:** every upload of open data, scheduled or manual, waits for the `OPEN_DATA_ENABLED` variable as well as H12, since a licence cannot be taken back once the files are mirrored. A manual dry run works now.
- **Alternatives:** the Open Government Licence v3.0 or ODbL for the derived figures; leave out TfL's reason text; weeks by their start date, which leaves the last week of a month incomplete on the 2nd.
- **Class:** `never-default` (publishing). **Recommendation:** accept, then set `OPEN_DATA_ENABLED`.
- **Settled 2026-11-15 (owner):** Accepted: CC BY 4.0 for our published figures and the open dataset, with "Powered by TfL Open Data" kept. `LICENSE` (MIT, code) and `LICENSE-DATA` (CC BY 4.0, figures) are added. `OPEN_DATA_ENABLED` remains an owner step after H12.

### D64. Iceberg maintenance covers the incremental tables, weekly

- **Context:** PLAN M7 asks for weekly `OPTIMIZE` and `VACUUM` on the curated tables. Only the staging models persist from run to run (incremental merges). Every other model is materialised as a table and rebuilt as a new table on each run, so it never collects small files or old snapshots.
- **Implemented** (`macros/iceberg_maintenance.sql`, runbook "Iceberg maintenance"): on London Sundays, after the build, `OPTIMIZE ... REWRITE DATA USING BIN_PACK` over the last 9 days of partitions, then `VACUUM`, on every incremental Iceberg model (today the 8 staging tables). VACUUM uses Athena's default snapshot age of 5 days, so time travel on staging reaches back about 5 days. It deletes only files no remaining snapshot uses; raw is never touched.
- **IAM:** the transform role's existing rights cover both statements: `glue:UpdateTable` on the prod database, and get, put and delete objects under the curated bucket's `prod/` prefix. No Terraform change.
- **Alternatives:** run it on every Iceberg table, which rescans tables rebuilt that morning for nothing; compact every partition each week; keep snapshots longer by setting `vacuum_max_snapshot_age_seconds` on the staging tables.
- **Class:** `auto-apply`. VACUUM is in the plan, and the scope only leaves out tables that have nothing to compact. **Default:** as implemented.
- **Settled 2026-11-15 (owner):** Accepted: weekly OPTIMIZE and VACUUM on the incremental tables as built.

## Settled

| Date | Decision | Outcome |
| --- | --- | --- |
| 2026-11-02 | Where the plan document lives | Local-only, not in git. |
| 2026-11-03 | Which status wins when a line has several (ADR-004, #5) | Highest weight wins. |
| 2026-11-03 | Deploy approval on a private repo on GitHub Free (ADR-005) | Merging is the approval, and merging deploys. |
| 2026-11-14 | D19 Disruption Index denominator (owner) | The recommended alternative: the time-weighted denominator. Disruption Index = 100 x sum of (severity weight x time weight) / sum of time weight over scored minutes, a time-weighted mean severity from 0 to 800. In `fct_line_daily`, `fct_line_weekly`, `rpt_monday_morning` and `publish/league.py`; the card subtitle describes the scale. |
| 2026-11-14 | D22 Planned statuses and lines mostly under works (owner) | The recommended option: a line is ranked only if it ran at least 50% of its scheduled hours (var `min_open_share_to_rank`, `MIN_OPEN_SHARE_TO_RANK`). Otherwise it keeps its index and shows "Not ranked: engineering works" in the marts (`unranked_reason`), the export, the share card and `post_snapshot.py`. Planned statuses other than closures keep their weights. D35 stays as it was and nests inside it (see D35). |
| 2026-11-14 | D36 Publish while decisions are open (owner) | The recommended option: publish with provisional labels. Cards and files keep the methodology note, provisional markers and `open_decisions`; `meta/methodology.json` lists the settled ones. Publishing still needs H12 and the `PUBLISH_ENABLED` variable (runbook, H12). The measured view stayed unpublished until D42 was settled the same day. |
| 2026-11-14 | D42 Publish the measured view (owner) | The recommended option: keep the D42 grain and denominators, and publish now that the gate passes under ADR-014. Measured fields (excess wait, long gaps, Honesty Gap, trains run, Experienced Reliability rank) are filled for each line-week whose own gate passes (D51); others carry `measured_withheld_reason`. The ADR-014 risk (boards that omit trains create gaps) is stated beside the Honesty Gap in the methodology. Contract `schema_version` 0.4.0. |
| 2026-11-14 | D44 Gate test severity (owner) | The recommended option: `gate_test_severity: error`. A failing gate turns `transform-daily` red and dbt skips everything downstream of `int_measured_hours`. Since D55 that is the measured tables only. |
| 2026-11-14 | D46 Accept ADR-014 (owner) | Accepted: the gate compares inferred trains with the trains on the boards; trains run against the timetable is its own measure. ADR-014 is marked accepted. |
| 2026-11-14 | D47 Line of the Week (owner) | As recommended: the largest fall in Disruption Index points among lines ranked and at least 90% observed in both weeks, with a null reason otherwise. |
| 2026-11-14 | D48 Wooden Spoon and worst incident (owner) | As recommended: the bottom-ranked line, split by line id; the incident with the most severity-weighted minutes. |
| 2026-11-14 | D49 Monday Morning Index (owner) | As recommended: the Disruption Index over each Monday 07:00 to 10:00 London time. |
| 2026-11-14 | D50 Engineering Works table (owner) | As recommended: weekly, per line, Part Closure counted in full. |
| 2026-11-14 | D55 A failing gate never holds back the declared view (owner) | Directed by the owner. The measured columns moved from `fct_line_weekly` to `fct_line_weekly_measured`, so the gate test at `error` skips only the measured models; the declared tables, league, divisions and season always build. Weeks the measured tables did not finish are withheld (`measured_not_rebuilt`). ADR-014, "Gate failures and the declared view"; checked by `tests/unit/test_dbt_gate_isolation.py`. |
| 2026-11-15 | D2, D3, D4 Accept ADR-007, ADR-008, ADR-009 (owner) | All three accepted. D1 / ADR-006 stays open until the Lambda concurrency quota is granted. |
| 2026-11-15 | D5 to D7, D9 Station budget, sqlfluff, plan checkbox, dbt unit tests in prod (owner) | The recommended option each: 50 stations, config kept, as written, tests kept. |
| 2026-11-15 | D8 Null-keyed prod rows (owner) | Settled by the D45 refresh. |
| 2026-11-15 | D10 Pin dbt-athena (owner) | Pinned to 1.11.x in `pyproject.toml`; `uv.lock` consistent. |
| 2026-11-15 | D11 ADR-010 (owner) | Accepted. The binding is in `infra/bootstrap`; it reaches AWS with `make bootstrap-apply` and the `all-roles` probe (owner steps). |
| 2026-11-15 | D12 to D15 Smoke-test poll, observer failures, automerge, ADR-011 (owner) | Accepted; ops jobs now fail when the observer role cannot be assumed; automerge removed; the merge gate stays. |
| 2026-11-15 | D16 to D18, D20, D21, D23, D24 M3 definitions and ADR-012, ADR-013 (owner) | All accepted as implemented. |
| 2026-11-15 | D25 to D35, D37 to D41, D43 Measured-view readings (owner) | All accepted. D27: Victoria and the eight lines link by vehicle id, District out. D25: same-destination condition is a later refinement. |
| 2026-11-15 | D45 `stg_arrivals` key and full refresh (owner) | Done: the prod refresh ran on 2026-11-14; transform-daily has passed since, including 493 of 493 on 2026-11-15. |
| 2026-11-15 | D51 to D54 Measured-view publishing (owner) | All accepted. The trains-run insight gets the caveat "Boards can miss trains that ran."; the 1200x675 card falls back to Good Service % if it does not fit. |
| 2026-11-15 | D56 to D61 League mechanics, ADR-015 (owner) | All accepted. D57 amended: a provisional month moves no one. D60: five 2027 strike rows added. |
| 2026-11-15 | D63, D64 Open data licence and Iceberg maintenance (owner) | CC BY 4.0 for the published figures; `LICENSE` and `LICENSE-DATA` added. Maintenance as built. |
| 2026-11-18 | D65, D66 Missing prediction directions and trains run without termini (owner) | Both accepted as implemented: a missing direction is filled from the same train (vehicle-id lines) or a one-way platform away from turnbacks, never for terminating trains or "Platform Unknown"; trains run is measured at core and branch stations only. Contract `schema_version` 0.8.0; W46 restated (Metropolitan trains run 67.5% to 76.7%, District 68.1% to 74.0%). |
