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

These come from the M3 bundle (`feat/m3-line-minute-metrics`). D16 to D23 are metric definitions, each implemented behind a named seed or variable, so changing the answer is a seed edit and the next build (ADR-012 rebuilds every minute from the seeds). D23 and D24 also deviate from the plan, through ADR-013 and ADR-012. They entered on 2026-11-08, so the reminder date is 2026-11-11 15:00 UTC. All are `never-default`.

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

### D19. Disruption Index denominator

- **Context:** PLAN 7.1 divides the time-weighted sum by "scheduled service hours" without saying whether the hours are time-weighted.
- **Implemented:** the literal reading. Disruption Index = 100 x sum(severity weight x time weight) / scored hours, i.e. weighted disruption minutes per 100 service hours. Scored hours exclude missing, engineering works and Service Closed minutes (PLAN 7, ADR-004). Values run from 0 to 96,000 (Suspended all peak). Week one ranged up to about 12,500 a day.
- **Alternative:** divide by time-weighted hours, which makes the index a weighted mean severity (0 to 800). Lines with more peak service then aren't favoured by a larger denominator.
- **Default:** none. **Class:** `never-default`.

### D20. Ranking ties and unranked lines

- **Context:** the plan doesn't say how ties are broken or whether provisional lines are ranked.
- **Implemented:** rank by Disruption Index ascending; ties go to the higher Good Service %; lines still equal share a rank. Lines with no scored minutes are unranked. Provisional line-weeks (completeness under `provisional_completeness: 0.9`) are ranked and flagged.
- **Alternatives:** leave provisional lines unranked; break ties by fewer incidents.
- **Default:** none. **Class:** `never-default`.

### D21. Time bands follow the London calendar day

- **Context:** PLAN 7.1 says weekday peaks weigh 2, other weekday service 1, weekends and Night Tube 0.75.
- **Implemented:** the band comes from the London calendar date and time of the minute (`time_bands.csv`, `day_types.csv`). So Night Tube minutes, which all fall on Saturday and Sunday mornings, are 0.75, and the 00:00 to 00:30 tail of Friday's normal service counts as weekend. The tail of Sunday's service after midnight counts as Monday, weekday off-peak (1).
- **Alternative:** band by service day, so Friday's late-night tail stays weekday.
- **Default:** none. **Class:** `never-default`.

### D22. Planned statuses other than closures still score

- **Context:** the plan exempts only closures (and planned Bus Service) from the score. On 2026-11-06, Suffragette had a planned Part Closure for 1,036 minutes, which left the score, and a planned Reduced Service for the other 74, which counted. Its index that day was 9,000 from 74 scored minutes.
- **Implemented:** the plan as written: planned Reduced Service and Special Service carry their weights.
- **The same mechanism:** under ADR-004 rule 3, a branch Part Closure takes every minute out of the score unless a weighted status sits on top of it. Delay minutes stay in while the Good Service minutes around them drop out, which inflates indexes for lines with long part closures.
- **Alternatives:** weight planned Reduced Service at 0, or treat it as engineering works; score part-closure minutes on the worst non-closure entry (Good Service included) instead of dropping them; require a minimum of scored hours before a line-day or line-week gets an index or rank.
- **Default:** none. **Class:** `never-default`.

### D23. Which entries describe a minute

- **Context:** ADR-004 reads the latest successful poll in the minute. Polls per minute can be more than one (post-deploy smoke test, ADR-006). ADR-013 (proposed) amends ADR-004's rules 1 and 4.
- **Implemented:** the latest successful poll in the minute that lists the line. An entry counts if it has no validity period or one overlapping the minute. TfL sometimes keeps listing a planned status a minute after its period ends, with no other entry for the line; that minute is missing (`missing_reason = no_current_entry`, 1 to 5 line-minutes a day).
- **Alternatives:** read only the very latest poll, so a later poll that omits a line makes the minute missing; keep a just-expired entry as current.
- **Default:** none. Accept or reject ADR-013. **Class:** `never-default`.

### D24. Declared-view intermediate models rebuild in full

- **Context:** PLAN 6.1 lists intermediate models as incremental merges. A status run or an incident can start before any incremental window, and the minute model applies seeds that D16 to D22 expect to change. A windowed merge would split runs and leave older weeks on old definitions, silently.
- **Implemented:** `int_line_minute_status`, `int_status_intervals`, `int_incidents`, `dim_service_hours` and the marts are Iceberg tables rebuilt from `raw_start_date` each run. Staging stays incremental. The cost is a few hundred MB scanned a year at most. See ADR-012 (proposed).
- **Default:** none: it deviates from the plan. Accept ADR-012, or ask for a seed-free incremental layer under a table that applies the seeds.
- **Class:** `never-default`.

### M4 decisions (D25 onwards)

These come from the M4 bundle (`feat/m4-measured-view`). They are readings of PLAN 7.2 and 7.3 where the plan is ambiguous. Each is implemented as the most conservative reading: an error should never make service look worse than it was, and missing data is never read as either good or bad service. They entered on 2026-11-08, so the reminder date is 2026-11-11 18:00 UTC. All are `never-default` (metric semantics). Several depend on the week-one data review (due from 2026-11-11). Those carry a recommendation to revisit once the review is in.

### D25. What counts as a duplicate prediction

- **Context:** PLAN 7.2 says to collapse duplicate predictions with different ids but "near-identical" `expected_arrival`, without a threshold.
- **Implemented** (`analysis/arrivals.py`, `duplicate_tolerance_seconds = 15`): within one board (station request, platform, direction) in one poll, predictions are ordered by expected arrival, then id. A prediction within 15 s of the row before it is dropped, and the earliest is kept. The comparison is with the previous row, not the previous kept row, so SQL can do it with `lag`.
- **Alternatives:** use the 45 s matching tolerance (two trains cannot share a platform 45 s apart, but that also merges real close pairs at platforms that serve two routes); keep the duplicate with the smallest `time_to_station`.
- **Recommendation:** keep 15 s until the review measures how far apart real duplicates sit.

### D26. The arrival rule reads a train's last sighting, and held trains split

- **Context:** PLAN 7.2 rule 2 says a train has arrived when it is within 120 s in poll *t* and absent in *t+1*, "or shows `time_to_station` = 0". Rule 1 links sightings whose expected arrivals are within 45 s across consecutive polls.
- **Implemented:**
  - A train's last sighting (one with no link to the next poll) is an arrival if the station's next successful poll exists and the sighting is at most 120 s out. It is also an arrival if no later poll exists and it shows 0 s.
  - Links are mutual nearest pairs within 45 s. That keeps them one to one and computable in SQL without recursion.
  - The 45 s rule is applied literally. A train held at a signal keeps a constant `time_to_station`, so its expected arrival moves by about 60 s per poll. It then fails to link, and each sighting within 120 s becomes an arrival.
- **Real data** (2026-11-05, Victoria line at Oxford Circus and King's Cross, both directions): the literal rule infers 2,225 arrivals. Linking by `vehicle_id` instead infers 1,874 (16% fewer), with about 32 trains an hour in the peaks and 24 at midday. The timetable has about 33 to 36 and 27.
- **Effect:** the split adds arrivals, which shortens headways. So the literal rule errs towards making service look better, which is the conservative side. But it can push line-hours over the 120% quality gate.
- **Alternatives:** link on order (trains cannot overtake on one platform) instead of a 45 s window; scale the window with the poll gap; link on `vehicle_id` where it is stable (D27).
- **Recommendation:** decide after the review measures expected-arrival drift in the last 120 s.

### D27. Which lines link sightings by vehicle id

- **Context:** PLAN 7.2 rule 1 uses `vehicle_id` "where it is stable", which is a week-one review question.
- **Implemented:** `InferenceParams.vehicle_id_lines` is empty, so every line uses the expected-arrival rule until the review names lines. On a listed line, two sightings with vehicle ids link only if the ids match. Sightings without an id fall back to expected arrival. DLR boards send no vehicle id at all.
- **Alternatives:** trust vehicle ids on all Tube lines now, since the D26 figures suggest they are stable on the Victoria line.
- **Recommendation:** fill the list from the review, line by line.

### D28. A board is a platform, and absence needs a successful poll

- **Context:** the plan says "for each platform and direction" and "absent in poll *t+1*", but not what a failed poll means.
- **Implemented:**
  - A board is (station request, NaPTAN id, line, platform name, direction). A null direction is its own board, which is common on Overground boards.
  - "Next poll" is the station's next successful arrivals poll. A failed poll neither confirms nor denies an arrival.
  - Each arrival carries `confirmed_at`, so a headway that spans a collection gap can be dropped in `int_headways` rather than read as a long gap.
  - A train that moves platform mid-approach ends on the first platform. If it is within 120 s there, that counts as an arrival there. This adds arrivals, the conservative side.
- **Alternatives:** follow trains per station and direction, across platforms.
- **Recommendation:** keep it, and count platform changes in the review.

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
- **Implemented:** `seed_line_frequencies` holds per-station trains per hour in each direction for those 23 stations. There are weekday peak (07:00 to 10:00, 16:00 to 19:00) and off-peak values, and Saturday and Sunday values, all `source = estimate_2026_11_08` and flagged `is_fallback`.
  - Values come from published service patterns. At mid-line stations they were checked against inferred arrivals on 2026-11-05 (vehicle-id linking). For example, Tottenham Court Road showed about 24 an hour per direction at peak and about 16 off-peak; Romford about 12 and 8; Canada Water about 16.
  - Where the two sources disagreed, the lower figure was used. A lower scheduled frequency means a longer scheduled headway, so service is never judged against a timetable busier than the real one.
  - Terminus boards over-count badly (for example, Watford Junction showed 41 at 21:00 against 3 an hour), so they were not used.
  - Only whole hours inside `line_operating_hours` are covered.
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

## Settled

| Date | Decision | Outcome |
| --- | --- | --- |
| 2026-11-02 | Where the plan document lives | Local-only, not in git. |
| 2026-11-03 | Which status wins when a line has several (ADR-004, #5) | Highest weight wins. |
| 2026-11-03 | Deploy approval on a private repo on GitHub Free (ADR-005) | Merging is the approval, and merging deploys. |
