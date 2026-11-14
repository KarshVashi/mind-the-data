# Decisions inbox

Decisions waiting on the owner. Every PR updates this page. When a decision is settled, move it to "Settled" with the date and the outcome.

## Classes

- `auto-apply`: if the owner says nothing by the deadline, the recommended default applies and is recorded under "Settled".
- `never-default`: nothing happens until the owner answers. This covers:
  - security boundaries, IAM or OIDC;
  - money or quotas;
  - metric semantics;
  - deviations from `docs/PLAN.md`;
  - publishing;
  - data deletion.

  For these, the deadline is only a reminder. The item stays pending until it is answered.

## Deadlines

The deadline is 72 hours after the item enters this inbox. Every item below entered on 2026-11-07, so the deadline is 2026-11-10 12:00 UTC.

## Pending

### D1. Accept ADR-006 (poller reserved concurrency waits for the quota)

- **Context:** the poller runs unreserved because the account's concurrency quota is 10. The plan's reservation of 1 is the guarantee against overlap and runaway concurrency.
- This deviates from the plan and depends on a quota.
- **Default:** accept. Switch to `1` once the quota is granted.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.

### D2. Accept ADR-007 (reference Lambda 1024 MB / 300 s)

- **Context:** the plan's 512 MB / 60 s is too small for the weekly reference fetch.
- The change is already deployed. It costs pennies a month, but it is a cost change and a deviation from the plan.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.

### D3. Accept ADR-008 (staging merges whole days on a row key)

- **Context:** the plan says staging is "incremental append". Append isn't idempotent, so staging merges on a key instead.
- That sets the grain of every staging model, and the metrics are built on those models.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.

### D4. Accept ADR-009 (CI roles trust immutable OIDC subjects)

- **Context:** the trust policy uses the prefix `repo:KarshVashi@41749592/mind-the-data@1442302649`. It has been live since #16, so accepting the ADR changes nothing deployed.
- It is an IAM trust boundary.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default`.

### D5. Station budget: 50 or about 55

- **Context:** `config/stations.yaml` uses all 50 distinct stations, at 52 calls a minute.
- About 55 adds branch ends for 57 calls a minute, still well under the key's 500. It means changing the loader's cap, the comment in `stations.yaml` and PLAN 5.2/5.3.
- **Default:** stay at 50. Revisit in the week-one review with real coverage numbers.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D6. sqlfluff strictness

- **Context:** `.sqlfluff` runs every rule with the jinja templater, a 100-character line limit and explicit aliasing. Any violation fails `make check`.
- The open question was whether to relax any rules.
- **Default:** keep the current config. Exclude a rule only when it trips, with the reason in a comment.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D7. Plan checkbox while the plan stays local

- **Context:** the plan is local-only by the owner's choice, so no PR can tick its checkboxes, which the workflow asks for.
- The local copy currently lags: M1 and the H-steps are still unticked.
- **Default:** tick the local plan when each PR opens, and track progress in this repo through STATUS.md. PR "Why" sections name the milestone and task by id ("M2, task 6").
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D8. Rebuild prod staging after #17

- **Context:** `transform-daily` ran in prod before #17. Then, arrivals and stop disruptions parsed as null-filled rows. A merge never deletes rows (ADR-008), so null-keyed rows may still sit beside the corrected ones.
- A `--full-refresh` rewrites the prod staging tables from raw.
- **Default:** check prod for null-keyed rows once AWS access is back. If any are found, run a full refresh in a one-off workflow run.
- **Now possible in CI:** `transform-daily` takes `full_refresh` and `select` on a manual run (runbook, "Full refresh in prod"), e.g. `-f full_refresh=true -f select='staging+'`, as the transform role.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `never-default` (data deletion).

### D9. dbt unit tests in prod builds

- **Context:** since #18, each daily prod `dbt build` creates and drops temporary Iceberg tables in the prod Glue database, one per unit test.
- The alternative is `--exclude-resource-type unit_test` in prod. CI has no warehouse access, so unit tests would then run only on manual dev builds. That gap is how the failures fixed in #18 went unnoticed.
- **Default:** keep running unit tests in prod until a dev unit-test workflow exists. That workflow needs an OIDC role, so it gets its own decision.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D10. Pin dbt-athena to a minor version

- **Context:** `pyproject.toml` allows `dbt-athena-community>=1.9,<2`, and `uv.lock` resolves 1.11.1.
- The code lives in `dbt-athena`, also 1.11.1. #18 overrides its `resolve_table_type` macro and was checked against 1.11.1.
- **Default:** pin `dbt-athena-community>=1.11.1,<1.12`, and add `dbt-athena>=1.11.1,<1.12`. Upgrade in a deliberate PR that runs the dev unit tests.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D11. ADR-010: bind OIDC roles to specific workflows

- **Context:** ADR-010 and its first step merged in #22 and were applied on 2026-11-08: the observer and publish roles, and the plan role on `main`, are bound to their workflow files. The `new-roles` trust probe passed the same day.
- The autonomy bundle (Part 4) sets `enforce_workflow_binding` to `true`, so the plan (pull request), apply and transform roles are bound too. It takes effect only with your `make bootstrap-apply`.
- **Default:** none. Accept ADR-010, merge the bundle, apply, then run the `all-roles` probe and the role checks (runbook H6, step 5).
- **Deadline:** 2026-11-11 12:00 UTC (72 hours after the bundle opened). **Class:** `never-default`.

### D12. Smoke test after each deploy invokes the poller

- **Context:** the deploy smoke test (autonomy bundle, Part 2) invokes the poller once after `apply`. Each deploy leaves one minute with two polls 20 to 40 s apart, the same overlap ADR-006 already allows. Later `int_` models must treat a minute as one or more polls.
- The alternative is to wait for the next scheduled object instead of invoking. It is slower and doesn't prove the deployed code runs on demand.
- **Default:** none: this touches metric semantics. Accept the extra poll, or ask for the wait-only check.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.

### D13. Ops checks when the observer role can't be assumed

- **Context:** `ops-daily` and `ops-daytime` (Part 3) skip with a warning, and stay green, when the role can't be assumed. If the observer trust broke later, the checks would go quiet.
- **Default:** fail the job when the role variable is set but the role can't be assumed. A two-line follow-up.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `auto-apply`.

### D14. Automerge allow-list

- **Context:** automerge (Part 3) is off until `AUTOMERGE_ENABLED` is `true`. Its allow-list is `transform/models/`, `tests/` and `docs/` (not `docs/adr/`). An auto-merged dbt model runs on `main` as the transform role, even after the binding in D11.
- The merge gate (Part 5, ADR-011) covers the same need with stricter checks: protected paths, the PR's own plan evidence and a pinned head. Automerge runs the gate's checks only in part, through its own allow-list.
- **Default:** none: it is a security boundary. The recommendation is to remove automerge in favour of the gate (a follow-up deleting `automerge.yml` and `ops/automerge.py`). The alternatives are to keep it off, or to turn it on with the list cut to `tests/` and `docs/`.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.

### D15. ADR-011: the engineer may merge low-risk pull requests through the gate

- **Context:** `make merge PR=<n>` merges only when the gate returns ALLOW. The gate runs `main`'s copy of `scripts/merge_gate.py`, `src/mind_the_data/ops/merge_gate.py` and `config/merge_policy.yaml`. Protected paths, IAM, deletes and any unlisted Terraform type always need you. The accepted risks are listed in ADR-011.
- If you accept it, the local working rule "never merge PRs" changes to allow `make merge` only. Until then nobody runs `make merge`; `make merge-check` is a dry run and changes nothing.
- **Default:** none. Accept, reject, or accept with `transform/**` protected too.
- **Deadline:** 2026-11-11 12:00 UTC. **Class:** `never-default`.

### M3 decisions (D16 to D24)

D19 and D22 were settled by the owner on 2026-11-14 and are under "Settled". These come from the M3 bundle (`feat/m3-line-minute-metrics`). D16 to D23 are metric definitions, each implemented behind a named seed or variable, so changing the answer is a seed edit and the next build (ADR-012 rebuilds every minute from the seeds). D23 and D24 also deviate from the plan, through ADR-013 and ADR-012. They entered on 2026-11-08, so the reminder date is 2026-11-11 15:00 UTC. All are `never-default`.

### D16. Service Closed (code 20) is weighted 0 and left out of the score

- **Context:** the plan has no weight for code 20. TfL sends it every night, roughly 01:15 to 04:15, and the week-one data has it on every line. With no seed row, `assert_line_status_codes_are_weighted` would fail every build.
- **Implemented:** `severity_weights.csv` code 20, weight 0, `is_service_closed = true`, `source = decision_d16`. A Service Closed minute inside scheduled hours (about 25 line-minutes a day, all at the edges of the hours seed) is counted in `service_closed_minutes` and left out of the numerator and denominator, like engineering works.
- **Alternatives:** count it as an in-service minute at weight 0 (it then reads as good service); weight it like Suspended (8), which would punish the edges of the hours seed; treat it as missing.
- **Other codes** TfL lists in `Line/Meta/Severity` (1 and 11 to 19) have not appeared and are left out on purpose, so the first one fails a test and comes back here.
- **Default:** none. **Class:** `never-default`.

### D17. Scheduled hours: timetables for 13 lines, estimates for 7

- **Context:** M3 needs fixed hours per line and day type. TfL timetables (`stg_timetable`, recorded 2026-11-05) cover the 11 Tube lines, DLR and Trams: the seed uses the first and last departures at the measurement stations, rounded out to 5 minutes, including Night Tube on Friday and Saturday. TfL returns 404 for Elizabeth line and Overground timetables.
- **Implemented:** `line_operating_hours.csv`. Elizabeth line and Overground use 05:30 to 00:30 (Sunday 07:30 to 00:00), cut back for Liberty (22:10) and Suffragette (00:00) where boards showed earlier last trains. Rows carry `source = estimate_2026_11_08`. Waterloo & City has no weekend hours, as its timetable has none. Public holidays use the calendar day; Overground night services are not modelled.
- **Alternatives:** hours from published TfL first/last train pages for the 7 lines; hours inferred from arrival boards (noisy: boards show predictions from 03:00); a public-holiday seed.
- **Default:** none. Confirm or supply hours for the 7 estimated lines.
- **Class:** `never-default`.

### D18. Incidents and recovery

- **Context:** PLAN 6.2 says an incident starts when a line leaves Good Service for an unplanned severity, ends when it returns, and gaps under 5 minutes merge. It doesn't say how missing minutes, closures or the end of service affect that, or what "recovery" measures.
- **Implemented** (`int_incidents`, var `incident_merge_gap_minutes: 5`):
  - an incident minute is a scored minute whose worst status is unplanned with a weight above 0;
  - every other observed minute is quiet, including minutes outside service hours, planned statuses and closures;
  - missing minutes are bridged: no data neither starts nor ends an incident;
  - incident minutes separated by fewer than 5 quiet minutes merge;
  - an incident belongs to the London date and week it starts in;
  - recovery = minutes from the first to the last incident minute of a closed incident; open incidents have no recovery yet and are left out of the median.
- **Alternatives:** let a long poller gap end an incident; measure recovery to the first Good Service minute specifically; count incidents in every day they touch.
- **Default:** none. **Class:** `never-default`.

### D20. Ranking ties and unranked lines

- **Context:** the plan doesn't say how ties are broken or whether provisional lines are ranked.
- **Implemented:** rank by Disruption Index ascending; ties go to the higher Good Service %; lines still equal share a rank. Lines with no scored minutes are unranked. Provisional line-weeks (completeness under `provisional_completeness: 0.9`) are ranked and flagged. Since D22 was settled (2026-11-14), a line that ran under half its scheduled minutes because of engineering works is also unranked.
- **Alternatives:** leave provisional lines unranked; break ties by fewer incidents.
- **Default:** none. **Class:** `never-default`.

### D21. Time bands follow the London calendar day

- **Context:** PLAN 7.1 says weekday peaks weigh 2, other weekday service 1, weekends and Night Tube 0.75.
- **Implemented:** the band comes from the London calendar date and time of the minute (`time_bands.csv`, `day_types.csv`). So Night Tube minutes, which all fall on Saturday and Sunday mornings, are 0.75, and the 00:00 to 00:30 tail of Friday's normal service counts as weekend. The tail of Sunday's service after midnight counts as Monday, weekday off-peak (1).
- **Alternative:** band by service day, so Friday's late-night tail stays weekday.
- **Default:** none. **Class:** `never-default`.

### D23. Which entries describe a minute

- **Context:** ADR-004 reads the latest successful poll in the minute. Polls per minute can be more than one (post-deploy smoke test, ADR-006). ADR-013 (proposed) amends ADR-004's rules 1 and 4.
- **Implemented:** the latest successful poll in the minute that lists the line. An entry counts if it has no validity period or one overlapping the minute. TfL sometimes keeps listing a planned status a minute after its period ends, with no other entry for the line; that minute is missing (`missing_reason = no_current_entry`, 1 to 5 line-minutes a day).
- **Alternatives:** read only the very latest poll, so a later poll that omits a line makes the minute missing; keep a just-expired entry as current.
- **Default:** none. Accept or reject ADR-013. **Class:** `never-default`.

### D24. Declared-view intermediate models rebuild in full

- **Context:** PLAN 6.1 lists intermediate models as incremental merges. A status run or an incident can start before any incremental window, and the minute model applies seeds that D16 to D22 expect to change. A windowed merge would split runs and leave older weeks on old definitions, silently.
- **Implemented:** `int_line_minute_status`, `int_status_intervals`, `int_incidents`, `dim_service_hours` and the marts are Iceberg tables rebuilt from `raw_start_date` each run. The M4 bundle adds `int_scheduled_headways` on the same terms: it applies the seeds of D33 and reads only the weekly timetables. Staging stays incremental. The cost is a few hundred MB scanned a year at most. See ADR-012 (proposed).
- **Default:** none: it deviates from the plan. Accept ADR-012, or ask for a seed-free incremental layer under a table that applies the seeds.
- **Class:** `never-default`.

### M4 decisions (D25 to D34, D40 to D45)

These come from the M4 work in the M4/M5 bundle. They are readings of PLAN 7.2 and 7.3 where the plan is ambiguous. Each is implemented as the most conservative reading available: missing data is never read as either good or bad service, and where the boards are ambiguous the error should not create a long gap that did not happen. Where a reading still carries a bias, the decision names it. They entered on 2026-11-08, so the reminder date is 2026-11-11 18:00 UTC. All are `never-default` (metric semantics). Several depend on the week-one data review (due from 2026-11-11). Those carry a recommendation to revisit once the review is in.

### D25. What counts as a duplicate prediction

- **Context:** PLAN 7.2 says to collapse duplicate predictions with different ids but "near-identical" `expected_arrival`, without a threshold.
- **Implemented** (`analysis/arrivals.py`, `duplicate_tolerance_seconds = 15`): within one board (station request, platform, direction) in one poll, predictions are ordered by expected arrival, then id. A prediction within 15 s of the row before it is dropped, and the earliest is kept. The comparison is with the previous row, not the previous kept row, so SQL can do it with `lag`.
- **Alternatives:** use the 45 s matching tolerance (two trains cannot share a platform 45 s apart, but that also merges real close pairs at platforms that serve two routes); keep the duplicate with the smallest `time_to_station`.
- **Recommendation:** keep 15 s until the review measures how far apart real duplicates sit.
- **Week-one review (2026-11-08):** keep 15 s and add a same-destination condition (Central close pairs are 93% two trains). **Code now:** 15 s, no destination condition, in both the reference and `int_inferred_arrivals`. It makes no difference on the Victoria line.

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

### D27. Which lines link sightings by vehicle id

- **Context:** PLAN 7.2 rule 1 uses `vehicle_id` "where it is stable", which is a week-one review question.
- **Implemented:** `InferenceParams.vehicle_id_lines` is empty, so every line uses the expected-arrival rule until the review names lines. On a listed line, two sightings with vehicle ids link only if the ids match and the expected arrival moved by no more than the poll gap plus 45 s. Without that bound, a placeholder id shared by several trains would chain them. Sightings without an id fall back to expected arrival. DLR boards send no vehicle id at all.
- **Alternatives:** trust vehicle ids on all Tube lines now, since the D26 figures suggest they are stable on the Victoria line.
- **Recommendation:** fill the list from the review, line by line.
- **Week-one review (2026-11-08):** Victoria now; Northern, Jubilee, Piccadilly, Central, Bakerloo, District, Hammersmith & City, Circle and Metropolitan after the Monday 9 November check; Elizabeth and Overground (trip ids) can switch on; not DLR, Waterloo & City or tram.
- **Code now (2026-11-09):** `seed_vehicle_id_lines` enables **Victoria only**, every row `decision_status = pending_owner_d26_d27`, with the review's advice per line in `review_recommendation`. To change a line, flip `link_by_vehicle_id` and rebuild. Placeholder ids `000` and `001` count as no id (var `placeholder_vehicle_ids`, and `InferenceParams.placeholder_vehicle_ids` in the reference).
- **Recheck (2026-11-13, [gate recheck](data-quality/2026-11-12-gate-recheck.md)):** ids are stable on Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern and Piccadilly; District is not (78.5% followed from Victoria to Earl's Court). Under the ADR-014 gate (D46) the unlinked lines' failing hours are almost all above 120%, the held-train double count linking removes; Piccadilly (74.1%) and Metropolitan (75.5%) are the two lines under 80%. **Recommendation:** link the eight lines. Still Victoria only until the owner answers.

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

### D29. Scheduled service is per station and direction, not per platform

- **Context:** PLAN 7.3 compares headways "at a platform and direction" with `int_scheduled_headways` "for that line, station, direction". TfL timetables have no platforms. At Earl's Court, the District inbound timetable lists two departures in the same minute 21 times on weekdays, one per eastbound platform.
- **Implemented:** `int_scheduled_headways` is per station and direction, pooling every timetable route.
- **Consequence for `int_headways` (not built yet):** pool a station-direction's platforms before forming headways. Per-platform headways at a two-platform station would read as about twice the scheduled ones, and become false long gaps.
- **Alternatives:** split scheduled departures across platforms using board history.
- **Recommendation:** pool, as implemented.

### D30. Which hour a gap belongs to, and service-day hours

- **Context:** PLAN 7.3 uses headways "in the hour" without saying which end of a gap sets the hour.
- **Implemented:**
  - A gap belongs to the hour of the departure that ends it, which is the train a waiting passenger boards. `int_headways` must use the same rule.
  - Hours are TfL's service-day clock (0 to 27). Hour 24 and later are the next calendar morning (`london_hour`, `is_next_calendar_day`).
  - Gaps are not formed across schedules: Friday's last Night Tube train and Saturday's first are in different schedules.
  - The sums of gaps and squared gaps are kept, so `int_headways` can add Friday hour 27 and Saturday hour 3, which fall in the same London hour.
- **Alternatives:** split a gap across the hours it spans; give it to the hour it starts in.
- **Recommendation:** keep it.

### D31. Two departures in one minute both count

- **Context:** TfL gives departure times to the minute. In the 2026-11-05 fetch, 127 journeys share their minute with another journey of the same route at the same station and direction, across all schedules. They are on the Jubilee, District, Victoria and Waterloo & City lines and the tram.
- At DLR Canning Town, each departure of route 1 is also listed in route 2 at the same minute (120 inbound on weekdays). That could be one train listed twice, or the Stratford International and Bank services leaving their two platforms together.
- **Implemented:** every listed journey counts, entered as a 0-minute gap. A 0 gap adds nothing to the sums in the average-wait formula, but it adds a scheduled train.
- If the Canning Town pairs are duplicates, scheduled trains there are overstated by about half. The quality gate would then exclude those hours rather than score them.
- **Alternatives:** collapse same-minute departures listed under different routes.
- **Recommendation:** decide Canning Town from its boards in the review. DLR boards there carry no vehicle ids and few predictions (see open questions in STATUS.md).

### D32. Timetable versions

- **Context:** the plan doesn't say which weekly timetable applies to which days.
- **Implemented:** the latest fetch of each UTC day is a version, valid from that date until the next version's date. The first version also covers the days collected before it (data starts 2026-11-01, the first fetch was 2026-11-05).
- If a later fetch fails, the last good version stays in force; `timetable_fetched_at` shows its age.
- **Alternatives:** use only the latest timetable for all history (a December timetable change would then rewrite November); treat a failed week as no timetable (unmeasured).
- **Recommendation:** keep it.

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

### D34. Scheduled headway for long gaps when gaps are uneven

- **Context:** PLAN 7.3 counts a long gap when a headway exceeds 2 x the scheduled headway, but the scheduled headway is ambiguous when an hour's gaps are uneven. On weekdays at 12:00, Victoria at Oxford Circus has a mean of 134 s and a longest gap of 180 s.
- **Implemented here:** `int_scheduled_headways` exposes both `mean_headway_seconds` and `max_headway_seconds`; `int_headways` will choose.
- **Recommendation for `int_headways`:** use the longest scheduled gap in the hour. Then a long gap is one that no scheduled gap that hour comes near, which is the conservative reading for the Honesty Gap headline.
- **Alternative:** the mean, which flags more gaps.

### M5 decisions (D35 to D39)

These come from the M5 publish work in the M4/M5 bundle. D35 to D38 entered on 2026-11-08, so the reminder date is 2026-11-11 22:00 UTC; D39 entered on 2026-11-09, reminder 2026-11-12 22:00 UTC. All are `never-default`. D35, D37 and D38 are listed in every published file's `open_decisions`. D36 was settled by the owner on 2026-11-14 and is under "Settled".

### D35. Mark lines scored on a small share of their hours as provisional

- **Context:** a line with long closures is scored only on the minutes left, and delays on top of a part closure stay in while the Good Service around them drops out. For 4 to 8 November, Suffragette had 94% of its scheduled minutes under closures and was ranked last on 5 scored hours; Tram had 42% and Mildmay 36%.
- **Implemented:** display only. A line with at least 25% of its scheduled minutes under closures (`CLOSURE_SHARE_PROVISIONAL` in `src/mind_the_data/publish/league.py`) gets a provisional marker on the cards, in the JSON (`provisional_reasons`, code `mostly_engineering_works`, decision `D35`) and in the numbers sheet. Its rank is unchanged (D20). A provisional line is never named in the insight line (D38), so this threshold also decides which line the headline can name.
- **With D22 settled (2026-11-14):** the two rules nest and do not contradict each other. Under 25% closed: ranked, no marker. 25% to 50%: ranked and provisional (this decision). Over 50%: not ranked, shown as "Not ranked: engineering works" (D22); the card does not add the provisional mark to such a row, since its own note already says why. Suffragette (94%) is now unranked; Tram (42%) and Mildmay (36%) stay ranked and marked. D35 is kept as it was; only its `decisions` list drops `D22`, which is no longer open.
- **Alternatives:** a different threshold; drop the marker now that D22 unranks the worst cases.
- **Default:** none. **Class:** `never-default` (metric semantics and publishing).

### D37. Typical bad hours on each line page

- **Context:** PLAN 8 names "typical bad hours" in `lines/{line_id}.json` without defining them.
- **Implemented:** the London hours of day with the highest share of scored minutes that carried a weight above 0, over the line's whole history, unweighted by time band; at most three, worst first, ties to the earlier hour. An hour needs at least 300 scored minutes (`MIN_BAD_HOUR_SCORED_MINUTES` in `documents.py`), so one disrupted quarter-hour on a mostly closed line is not called a pattern. Planned Reduced Service counts as disrupted (D22).
- **Alternatives:** weight by time band or severity; count only unplanned statuses; a rolling window (the last 12 weeks) instead of the whole history, which also bounds the scan as data grows.
- **Default:** none. **Class:** `never-default` (metric semantics).

### D38. The share card's insight line without the measured view

- **Context:** M5 fills the insight line from the week's biggest Honesty Gap, which needs M4.
- **Implemented:** the biggest Honesty Gap when any line has one; otherwise the lowest Good Service % among lines that are not provisional, worded "showed Good Service for only X% of its scored service time" (scored time leaves out closures, Service Closed and missing minutes). With every line provisional there is no insight, and the card shows a neutral line.
- **Alternatives:** the highest Disruption Index; the longest incident; write the insight by hand for each post.
- **Default:** none. **Class:** `never-default` (publishing and metric wording).

### D39. Rewrite every archived week on each publish

- **Context:** the website needs an archive index (`meta/index.json`, see `docs/site-integration.md`). `lines/{line_id}.json` is already rebuilt from the current marts on every run, so a league file written once and never touched again would drift from the line pages after any metric change or backfill.
- **Implemented:** each export run writes `league/{iso_week}.json` for every complete week with observed data, `league/latest.json` for the newest complete week (whichever week was asked for), and `meta/index.json` listing exactly those weeks and the line files. Past weeks therefore always show the current methodology; `generated_at` and `git_sha` say which. Share cards are still rendered only for the week asked for, so a card is a record of what was posted.
- **Alternatives:** write each week once and freeze it (the archive then shows the methodology of its day, and the index must be built from an R2 listing at upload time); freeze weeks only after D16 to D23 are settled.
- **Default:** none. **Class:** `never-default` (publishing).

### M4 decisions, continued (D40 to D45)

These came out of the week-one review and the measured-view review, after D35 to D39 had been taken by the M5 work. They entered on 2026-11-09, so the reminder date is 2026-11-12 18:00 UTC. All are `never-default`.

### D40. Time to go is read from the poll, not TfL's `time_to_station`

- **Context:** the week-one review (Q4) found `time_to_station` counts from the prediction's own timestamp, about 40 s before the poll, and repeats between polls. A train whose last sighting shows 121 s can be 16 s from the platform, so the 120 s arrival rule dropped it.
- **Implemented:** every time-to-go rule (the 120 s threshold, "shows 0", the standing link) reads `expected_arrival - polled_at` in whole seconds, truncated as Athena's `date_diff` does and floored at 0. TfL's figure stays on the output as `last_time_to_station`. On the Victoria check this is the review's variant C (+2.7 points over variant B).
- **Alternatives:** keep `time_to_station` and raise the threshold to 180 s (variant B gains 2.5 points).
- **Class:** `never-default` (metric semantics). **Recommendation:** keep.

### D41. Measured-view models rebuild in full

- **Context:** PLAN 6.1 makes intermediate models incremental merges. An inferred arrival depends only on its poll and the station's next one, but an arrival at the end of the data is provisional, and a merge cannot remove one that a later poll turns into a moving train.
- **Implemented:** `int_inferred_arrivals`, `int_headways` and `int_measured_hours` are tables rebuilt each run, as D24 did for the declared view (ADR-012, proposed). On dev, `int_inferred_arrivals` builds in about 20 s from five days of staging.
- **Cost:** the build reads ten columns of `stg_arrivals` over all history, about 1.4 million rows a day; a year is about 0.5 billion rows. Once the scan passes about 5 GB a run, switch to an incremental merge that rewrites whole days and trims the last 30 minutes of each run.
- **Class:** `never-default` (deviates from the plan). **Recommendation:** accept for now, revisit in M7.

### D42. How the measured view aggregates

- **Context:** PLAN 7.2 and 7.3 leave the grain of the gate and the denominators open.
- **Implemented:**
  - The gate is judged per line, station, direction and London hour (the review's station-direction-hour), platforms pooled (D29). Null-direction boards are left out.
  - An hour is observed when the station had at least 54 successful arrivals polls (var `min_polls_per_hour`), no gap over 150 s between polls touches it, and it is a single UTC hour. The October fall-back 01:00 happens twice in London time and would hold two hours of trains against one of timetable, so it is not observed. Only observed hours are gated; a poller gap is missing data, not a failed hour.
  - A headway counts only when every London hour it spans has scheduled service at that station and direction, so the overnight break is not a gap.
  - Weekly excess wait is the mean over gated hours with at least one counted headway (`excess_wait_hours`; each is one service hour at one station-direction). Long gaps per 100 hours divide by all gated station-direction-hours (`measured_hours`).
  - A line is ranked on the measured view only when at least half its scheduled station-direction-hours pass the gate (`measured_share`, var `min_measured_share`); below that it is `is_measured_provisional` and unranked, like the declared view's 90% completeness rule.
  - The Honesty Gap's denominator is whole-hour Good Service hours with at least one gated measurement of the line. A long gap belongs to the hour of the arrival that ends it (D30), and only that hour's declared status is checked: a gap that began in a minute of declared delay in the previous hour still counts if the ending hour was all Good Service. That reads the Honesty Gap slightly high; requiring Good Service over the gap's whole span is the alternative.
  - The gate's exclusion takes the worst hours out: a suspension leaves an hour under 80% of the timetable, so it drops out of excess wait, long gaps and the Honesty Gap. This follows PLAN 7.2 literally; under-band hours are kept in `int_measured_hours` for a separate count if wanted. **Since ADR-014 (2026-11-14)** this no longer holds: the gate compares with the trains on the boards, so a thin hour the boards agree on stays in.
  - Trains run against the timetable (`trains_run_share`, ADR-014) sums observed and scheduled trains over observed station-direction-hours, all measurement stations.
- **Known bias:** a train the inference misses joins two gaps into one, so long gaps and the Honesty Gap read high while the gate sits at 80% to 90% of the timetable. On 2 to 8 November the Tube lines show 19 to 99 long gaps per 100 measured hours, and the Honesty Gap 35% to 81% of Good Service hours. These are not results.
- **Alternatives:** gate per line-hour pooled over stations; long gaps per 100 scheduled service hours from `dim_service_hours`.
- **Class:** `never-default` (metric semantics). **Recommendation:** keep the grain; do not publish the Honesty Gap before the gate passes.
- **Still open (2026-11-14).** The gate passes under ADR-014 (87.1% on 9 to 13 November), but publishing the measured fields, trains run included, waits for this answer: every export keeps them `null` with `measured_status: not_measured`.
- **Before publishing:** if the boards leave out trains that ran, hours that now pass the gate carry gaps that exist only on the boards, and those feed the Honesty Gap. The recheck's open item 2 (is the 15% shortfall real service?) should be answered first (ADR-014, consequences).

### D43. Lines that are not measured

- **Context:** the week-one review found four lines that cannot be measured as things stand.
- **Implemented:** `seed_measured_lines` sets `is_measured = false` for the DLR (no vehicle ids, minute-quantised estimates, 30% to 135% of the timetable), the tram (closures and unscheduled directions), Waterloo & City (both stations are termini) and the Suffragette line (closed at both stations during the review). `fct_line_weekly` shows them as `not_measured` with no measured numbers or rank. Every row is `pending_owner`; flip the flag and rebuild to change one.
- **Alternatives:** measure the DLR at Canning Town only; keep the tram and Suffragette in once their closures end (after 10 November).
- **Class:** `never-default` (metric semantics, publishing). **Recommendation:** keep until the review is re-run on fixed staging and after the closures.

### D44. The M4 gate test warns until the acceptance passes

- **Context:** PLAN M4 asks for a test that fails when more than 30% of core-station line-hours fall outside 80% to 120%. On 5 to 7 November 65.6% of core-station hours on measured Tube lines are in band (34.4% out); over 2 to 8 November 66.2%. If no hour is observed in the window, the test also reports it, rather than passing. A failing test would stop every daily prod transform.
- **Implemented:** `assert_measured_hours_pass_the_gate` takes its severity from the var `gate_test_severity`, `warn` for now. Every build shows the shortfall without blocking the declared view.
- **Alternatives:** `error` now, which stops `transform-daily` until the gate passes.
- **Class:** `never-default` (deviates from the plan). **Recommendation:** set it to `error` once the gate passes.
- **Update (2026-11-14):** under ADR-014 (D46) the gate passes: 87.1% of judged core-station Tube hours in band on 9 to 13 November, 87.3% over the test's own last seven days (12.6% out against the 30% limit), and the test no longer warns on dev. The condition for `error` is met. The var stays `warn` until the owner says so; to switch, set `gate_test_severity: error` in `transform/dbt_project.yml`. A failing gate would then stop `transform-daily`.

### D45. `stg_arrivals` keys on the prediction, and prod needs a full refresh

- **Context:** the week-one review (Q5) found `stg_arrivals` keyed on `(poll_id, request_path, prediction_id)`. TfL's id is not unique on a board: every DLR prediction at a station shares one id, and a train that terminates and leaves again keeps its id on two platforms. On 6 November staging dropped 83.7% of DLR rows and up to 38% at Tube termini. This is a bug fix to merged M2 staging.
- **Implemented:** the key adds line, stop, platform, direction, vehicle, destination and expected arrival. On dev, 6 November goes from 1,250,353 to 1,444,004 rows against 1,444,816 raw (the 812 left are the same prediction listed twice). `assert_staged_arrivals_match_raw` fails when staged rows differ from the distinct raw predictions of any line-day, either way: fewer means a key narrower than a prediction, more means rows left by an older key. TfL does list one prediction twice (8% of Piccadilly rows overnight), so raw rows are not compared one for one.
- **Prod:** the merge key changed, so an incremental run would keep the old rows beside the new ones for the days it rereads, and older days stay short. Prod needs `dbt build --full-refresh --select stg_arrivals+` once after merge (owner step in STATUS.md). The dev table was rebuilt this way. Since M7 this runs in CI: `gh workflow run transform-daily.yml --ref main -f full_refresh=true -f select='stg_arrivals+'`.
- **Class:** `never-default` (data rewrite). **Recommendation:** accept, and run the full refresh straight after merge.

### M6 and gate decisions (D46 to D50)

These come from the M6 and gate bundle (`feat/m6-league-and-gate`). They entered on 2026-11-14, so the reminder date is 2026-11-17 18:00 UTC. All are `never-default`. D47 to D50 are listed in every published file's `open_decisions`.

### D46. Accept ADR-014 (the gate tests inference fidelity)

- **Context:** the 12 November recheck found the boards list about 85% of timetabled trains while the inference matches the distinct vehicle ids on the boards almost exactly, so the PLAN 7.2 band against the timetable confused trains not run with inference error. The owner approved writing the ADR on 2026-11-14.
- **Implemented ([ADR-014](adr/014-gate-tests-inference-fidelity.md)):**
  - `int_vehicle_passes` counts trains on the boards by vehicle id;
  - `int_measured_hours` gates on inferred ÷ observed trains within 80% to 120%, judged where a train was on the boards or inferred (inferred trains with none on the boards fail);
  - trains run against the timetable is reported per hour and per line-week (`trains_run_share`), withheld for Circle and Hammersmith & City, whose boards misfile trains;
  - the gate test uses the same measure.
- **Result:** 87.1% of judged core-station Tube hours in band on 9 to 13 November ([data-quality page](data-quality/2026-11-14-fidelity-gate.md)), against 65.4% under the old band.
- **Not built:** assigning Circle and Hammersmith & City trains on the shared section. The boards give both lines' westbound trains the same destination, so destination cannot split them; vehicle-id history can, and that is a follow-up in the ADR.
- **Alternatives:** keep the plan's band (the M4 acceptance stays unmet while the inference is right); lower the band to 70% of the timetable; a ±10% fidelity band (fails one in eleven good hours on the Victoria line).
- **Class:** `never-default` (deviates from the plan; metric semantics). **Recommendation:** accept. Then decide D44 (`error`) and D42 (publish the measured view).

### D47. Line of the Week

- **Context:** PLAN M6 names "Line of the Week (most improved)" without a measure or a rule for a missing week.
- **Implemented** (`rpt_weekly_highlights`, mirrored in `publish/league.py`):
  - the largest fall in Disruption Index since the previous week, in index points (D19 scale), among lines ranked and at least 90% observed in both weeks ("not provisional" here means completeness only; a line 25% to 50% under works, marked under D35, can still be named);
  - ties go to the lower index this week, then line id;
  - null with a reason when the previous week has no data (`no_previous_week`), had not ended or was under 90% observed (`previous_week_incomplete`), or no line improved (`no_line_improved`); a custom window has none (`custom_window`).
- **Now:** W45 has `no_previous_week`; W46 (9 to 15 November) will have `previous_week_incomplete`, since W45 began on Wednesday. W47 (16 to 22 November) is the first with a Line of the Week.
- **Python and SQL:** `publish/league.py` computes the same rule from `fct_line_weekly` rows for the export; on dev both give W45 `no_previous_week` and W46 `previous_week_incomplete`, and the same Wooden Spoon and worst incident. `test_line_of_the_week_by_hand` runs the dbt unit test's weeks through the Python.
- **Alternatives:** relative improvement (percent), which favours lines with a small index; rank places climbed (`rank_movement`), which ties often; exclude D35-marked lines too.
- **Class:** `never-default` (metric semantics, publishing).

### D48. Wooden Spoon and the single worst incident

- **Context:** PLAN M6 names both without rules.
- **Implemented:**
  - Wooden Spoon: the bottom-ranked line of the week. Lines sharing the bottom rank are split by line id, with `shared_by` giving the count. A provisional line can take it (it is ranked, D20). In the published JSON its `is_provisional` is the line's own flag in `lines[]` (low completeness or the D35 closure marker); `rpt_weekly_highlights.wooden_spoon_is_provisional` holds completeness only, since D35's threshold lives in the publish code. A line not ranked for works (D22) cannot take it.
  - Worst incident: the incident starting in the window with the most severity-weighted minutes (each incident minute at its severity weight, no time weight; `int_incidents.severity_weighted_minutes`). Ties go to the longer, then the earlier. This replaces "the longest" in the league file's `worst_incident`. Bridged missing minutes add nothing.
- **Alternatives:** exclude provisional lines from the spoon (as D38 does for the insight); weight incidents by time band too, so peak disruption counts double.
- **Class:** `never-default` (metric semantics, publishing).

### D49. Monday Morning Index

- **Context:** PLAN 6.2 lists `rpt_monday_morning` ("weekday 07:00 to 10:00 only"); M6 asks for the Monday AM peak for each line.
- **Implemented** (`rpt_monday_morning`): the Disruption Index over each Monday's `am_peak` band (07:00 to 10:00 London), with the weekly table's rules (D19, D20, D22, completeness). A bank-holiday Monday is still a weekday (D21). Every minute weighs 2, so it is the plain mean severity x 100. Published in each league file for the Mondays in the window; a Monday nothing observed is left out.
- **Alternatives:** every weekday morning of the week (the plan's wording in 6.2); the whole Monday.
- **Class:** `never-default` (metric semantics, publishing).

### D50. The Engineering Works table is weekly

- **Context:** PLAN 6.2 gives `rpt_engineering_works` the grain line x month; the M6 brief asks for hours closed per line per week.
- **Implemented:** `rpt_engineering_works` per line and ISO week: minutes whose worst declared status is a closure, in hours, with the share of scheduled minutes, whether D22 left the line unranked, and a rank by hours (every line has a row). A Part Closure takes the whole line-minute (ADR-004), so a branch closure counts in full. The league file lists only lines with works, most first, without the rank, and works the same figures out from the window's minutes so a custom window gets them too. A monthly view is a sum of weeks and can follow.
- **Alternatives:** monthly, as PLAN 6.2; count only Planned Closure, not Part Closure.
- **Class:** `never-default` (deviates from the plan).

### M7 decisions (D63, D64)

These come from the M7 hardening work (`feat/collate-c-m7`). They entered on 2026-11-14, so the reminder date is 2026-11-17 18:00 UTC.

### D63. The open dataset's contents and licence

- **Context:** PLAN 8 lists `open-data/{yyyy-mm}/*.parquet` (M7) without contents or a licence. Republishing derived TfL data needs the attribution "Powered by TfL Open Data", and the project's own figures need a licence of their own.
- **Implemented** (`publish/open_data.py`, runbook "Monthly open data"):
  - one London calendar month per folder, exported on the 2nd;
  - `line_minute_status` (every line-minute, missing minutes flagged, TfL's reason text included), `line_daily` (`fct_line_daily`) and `line_weekly` (the declared-view columns of `fct_line_weekly` for the weeks that end in the month);
  - the measured view left out while D42 is open;
  - a README and `manifest.json` in each folder, with the TfL attribution and terms link, and the derived figures offered under **CC BY 4.0**.
- **Not yet live:** every upload of open data, scheduled or manual, waits for the `OPEN_DATA_ENABLED` variable as well as H12, since a licence cannot be taken back once the files are mirrored. A manual dry run works now.
- **Alternatives:** the Open Government Licence v3.0 or ODbL for the derived figures; leave out TfL's reason text; weeks by their start date, which leaves the last week of a month incomplete on the 2nd.
- **Class:** `never-default` (publishing). **Recommendation:** accept, then set `OPEN_DATA_ENABLED`.

### D64. Iceberg maintenance covers the incremental tables, weekly

- **Context:** PLAN M7 asks for weekly `OPTIMIZE` and `VACUUM` on the curated tables. Only the staging models persist from run to run (incremental merges). Every other model is materialised as a table and rebuilt as a new table on each run, so it never collects small files or old snapshots.
- **Implemented** (`macros/iceberg_maintenance.sql`, runbook "Iceberg maintenance"): on London Sundays, after the build, `OPTIMIZE ... REWRITE DATA USING BIN_PACK` over the last 9 days of partitions, then `VACUUM`, on every incremental Iceberg model (today the 8 staging tables). VACUUM uses Athena's default snapshot age of 5 days, so time travel on staging reaches back about 5 days. It deletes only files no remaining snapshot uses; raw is never touched.
- **IAM:** the transform role's existing rights cover both statements: `glue:UpdateTable` on the prod database, and get, put and delete objects under the curated bucket's `prod/` prefix. No Terraform change.
- **Alternatives:** run it on every Iceberg table, which rescans tables rebuilt that morning for nothing; compact every partition each week; keep snapshots longer by setting `vacuum_max_snapshot_age_seconds` on the staging tables.
- **Class:** `auto-apply`. VACUUM is in the plan, and the scope only leaves out tables that have nothing to compact. **Default:** as implemented.

## Settled

| Date | Decision | Outcome |
| --- | --- | --- |
| 2026-11-02 | Where the plan document lives | Local-only, not in git. |
| 2026-11-03 | Which status wins when a line has several (ADR-004, #5) | Highest weight wins. |
| 2026-11-03 | Deploy approval on a private repo on GitHub Free (ADR-005) | Merging is the approval, and merging deploys. |
| 2026-11-14 | D19 Disruption Index denominator (owner) | The recommended alternative: the time-weighted denominator. Disruption Index = 100 x sum of (severity weight x time weight) / sum of time weight over scored minutes, a time-weighted mean severity from 0 to 800. In `fct_line_daily`, `fct_line_weekly`, `rpt_monday_morning` and `publish/league.py`; the card subtitle describes the scale. |
| 2026-11-14 | D22 Planned statuses and lines mostly under works (owner) | The recommended option: a line is ranked only if it ran at least 50% of its scheduled hours (var `min_open_share_to_rank`, `MIN_OPEN_SHARE_TO_RANK`). Otherwise it keeps its index and shows "Not ranked: engineering works" in the marts (`unranked_reason`), the export, the share card and `post_snapshot.py`. Planned statuses other than closures keep their weights. D35 stays as it was and nests inside it (see D35). |
| 2026-11-14 | D36 Publish while decisions are open (owner) | The recommended option: publish with provisional labels. Cards and files keep the methodology note, provisional markers and `open_decisions`; `meta/methodology.json` lists the settled ones. Publishing still needs H12 and the `PUBLISH_ENABLED` variable (runbook, H12). The measured view stays unpublished (D42). |
