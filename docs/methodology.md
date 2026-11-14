# Methodology

How the Tube League Table turns Transport for London's live data into a weekly ranking of London rail lines, what each number means, and where the numbers are weak.

The table ranks every line two ways:

- **Declared view, the Disruption Index:** what TfL's own status feed said about the line, minute by minute, weighted by severity and time of day.
- **Measured view, Experienced Reliability:** how long passengers actually waited, inferred from the arrival boards at a fixed set of stations, against the timetable.

The gap between the two, hours in which TfL said Good Service while the boards showed a long gap, is the **Honesty Gap**.

**Status, methodology v0.2.** The declared view is built and runs daily; the weekly files that carry it are ready. On 14 November 2026 the owner settled three decisions: the Disruption Index is a time-weighted mean severity on a 0 to 800 scale (D19), a line is ranked only if it ran at least half its scheduled hours (D22), and publishing goes ahead while other definitions are open, with provisional labels (D36). Public publishing starts once the Cloudflare setup is done. The measured view is built and its quality gate now passes on the definition in ADR-014 (proposed), but it **will not be published** until the owner decides to (D42). Most definitions below are readings of an ambiguous rule and are waiting for a final decision. Each is tagged with its decision number (`D16`) from [DECISIONS.md](DECISIONS.md) or its record (`ADR-004`) in [adr/](adr/), and the table at the end says which are provisional. Every published file lists the open decisions it rests on in `open_decisions`.

---

## 1. Data

### What is collected

A collector polls TfL's Unified API every minute and keeps every response unchanged (ADR-002, ADR-003):

| Feed | Cadence | Used for |
| --- | --- | --- |
| Line status (Tube, Elizabeth line, DLR, Overground, tram) | every minute | the declared view |
| Line disruption | every minute | reason text for incidents |
| Arrivals at about 50 measurement stations | every minute | the measured view |
| Stop disruption | every 5 minutes | context |
| Timetables, route sequences, severity codes | weekly | scheduled service, reference |

Collection started on **4 November 2026**. None of it can be backfilled, so the first week (2 to 8 November) is partial.

Failed requests are stored too, with their HTTP status and an empty payload. That is what lets the pipeline tell "no data" from "no disruption".

### Measurement stations

`config/stations.yaml` lists 2 to 5 stations per line (2 only where a line is that short): busy core stations every train calls at, plus stations that see every train for a branch. One Arrivals call returns every line serving a station, so interchanges count for several lines. The choice of each station is justified in that file.

### Time

Everything is stored in UTC. London time is applied only in the transformation, from seeded time bands and day types, so a clock change is handled in one place. A week is an ISO week, Monday 00:00 to Sunday 23:59 London time.

### Missing data is never Good Service

A minute with no successful poll, or a poll that has no entry for the line, is **missing**. Missing minutes leave both the numerator and the denominator of every score. They are never read as Good Service, and never as a delay.

**Completeness** is the share of a line's scheduled minutes that were observed. It sits beside every score. A line-week under 90% completeness is marked **provisional**: it is still ranked, with the flag and the reason shown (D20).

---

## 2. Declared view: the Disruption Index

### The status of a minute

For each line and each London minute inside its scheduled service hours (D17), the pipeline takes the latest successful poll in that minute that lists the line, and every status entry in it whose validity period covers the minute (ADR-013, D23). When several statuses apply at once, **the highest severity weight wins**; ties go to the lowest TfL code. A planned closure never hides an unplanned delay on top of it (ADR-004).

### Severity weights

From `transform/seeds/severity_weights.csv`:

| TfL status | Weight |
| --- | --- |
| Good Service | 0 |
| Minor Delays | 1 |
| Special Service | 1 |
| Reduced Service | 1.5 |
| Severe Delays | 3 |
| Part Suspended | 5 |
| Suspended | 8 |
| Bus Service | 5 unplanned, 0 planned |
| Planned Closure, Part Closure | excluded (engineering works) |
| Service Closed | excluded (D16) |

An unknown status code fails a test and stops the build. It is never given a default weight.

Planned statuses other than closures keep their weight: a planned Reduced Service scores 1.5.

### Time weights

From `transform/seeds/time_bands.csv`, by the London calendar date and time of the minute (D21):

| When | Weight |
| --- | --- |
| Weekday 07:00 to 10:00 and 16:00 to 19:00 | 2 |
| Other weekday service | 1 |
| Saturday and Sunday, including Night Tube | 0.75 |

Because the band follows the calendar day, Friday's service after midnight counts as Saturday, and Sunday's after midnight counts as Monday off-peak (D21).

### Scored minutes

A minute is **scored** when it is scheduled, observed, and not under a closure or Service Closed. Engineering works are left out of the reliability score and reported separately (PLAN 7.4).

### The index

```
Disruption Index = 100 × Σ (severity weight × time weight) over scored minutes
                       ÷ Σ (time weight) over the same minutes
```

That is the time-weighted average severity of the line's scored minutes, times 100, on a scale from **0** (Good Service in every scored minute) to **800** (Suspended in every scored minute). Lower is better. A peak minute counts twice in both the top and the bottom, so a line with more peak service is not favoured by a larger denominator, and a disrupted peak still moves the average twice as much as an off-peak minute (D19, decided 2026-11-14).

Worked example: a weekday with 60 scored peak minutes (weight 2) and 120 off-peak minutes (weight 1), of which 30 peak minutes were Severe Delays (3). Σ (severity × time) = 30 × 3 × 2 = 180; Σ time weight = 60 × 2 + 120 × 1 = 240; index = 100 × 180 / 240 = 75.

### Supporting numbers

- **Good Service %:** the share of scored minutes at Good Service.
- **Incidents per 100 service hours.** An incident starts when a scored minute carries an unplanned status with a weight above 0, and ends when the line goes quiet. Incident minutes fewer than 5 quiet minutes apart merge. Missing minutes neither start nor end an incident. An incident belongs to the day and week it starts in (D18).
- **Median recovery:** minutes from the first to the last incident minute of each closed incident (D18).
- **Typical bad hours** on each line page: up to three London hours of the day with the highest share of disrupted scored minutes over the line's history, each with at least 300 scored minutes (D37).

### Ranking

Lines are ranked by Disruption Index, lowest first. Ties go to the higher Good Service %, and lines still equal share a rank. A line with no scored minutes is unranked (D20).

**Engineering works.** A line is ranked only if it ran at least 50% of its scheduled hours; engineering works took the rest. Otherwise it keeps its index but shows as **"Not ranked: engineering works"** (D22, decided 2026-11-14). Between 25% and 50% under closures, a line is ranked and marked provisional, because it is ranked on what is left of its week (D35). A provisional or unranked line is never named in the card's headline (D38).

### Weekly highlights

- **Wooden Spoon:** the bottom-ranked line of the week. Lines sharing the bottom rank are split by line id, and the file says how many share it (D48).
- **Line of the Week:** the largest fall in Disruption Index since the previous week, among lines ranked and not provisional in both weeks. There is none, with a stated reason, when the previous week has no data, was not complete or was under 90% observed, or when no line improved (D47). The first full week, 9 to 15 November, has none, because the week before began on a Wednesday.
- **Worst incident:** the incident starting in the week with the most severity-weighted minutes, each incident minute counted at its severity weight. Ties go to the longer, then the earlier (D48).
- **Monday Morning Index:** the Disruption Index over Monday 07:00 to 10:00 for each line, ranked the same way (D49).
- **Engineering Works:** hours each line's worst status was a closure, per week, most first (D50).
- **Recovery Time:** each line's median recovery over the week's closed incidents, quickest first; ties go to fewer incidents. **Provisional**: it rests on D18, which is still open, and every published copy says so (D59).

### Divisions

Two divisions of ten lines (D57). They are seeded by Disruption Index over the first **two** complete weeks (9 to 22 November 2026, once the second ends), not the four the plan set, and the seeding is marked **provisional** until four complete weeks exist ([ADR-015](adr/015-divisions-seed-from-two-weeks.md), D56). A complete week is one every line has finished with at least 90% of the network's minutes observed. Until the seeding weeks exist, the month's file says "not yet seeded" and shows no table.

Divisions play from December 2026. Each month, lines are ranked within their division by the month's Disruption Index, with the weekly rules. At the end of the month the bottom three ranked lines of Division One swap with the top three of Division Two. Lines level on index and Good Service % at a promotion or relegation place are split by line id. A line not ranked that month (mostly closed for works, or nothing scored) neither goes up nor down.

### Season

The season runs from 1 April to 31 March and resets each April. Its table is the Disruption Index over every date of the season so far, ranked like the weekly table. The 2026-27 season starts with the collector on 4 November 2026, so until April 2027 it is the season to date (D58).

### Strikes

Strikes are kept in the main table: they are part of what passengers get. The industrial action seed lists 2026 action on the lines in scope, each row with its public source. Each week's file also carries a **strikes-excluded** table. It leaves out, for each line, the whole London dates of action that was held or announced for it (a noon-to-noon strike removes both days), and ranks the rest of the week the same way. In a week with no action it is identical to the main table, and a test checks that on every build (D60).

### Sensitivity

The weights are a judgment, so each published week is re-ranked under three alternative weight sets (PLAN 7.5, D61):

- every minute weighing 1;
- severity weights 1 to 5 by order instead of 1 to 8;
- peaks weighing 3 and weekends 0.5.

`meta/methodology.json` reports, for each week and set:

- Spearman's rank correlation with the published ranks;
- the largest change of rank;
- whether the top three or bottom three lines change.

On the first published week (4 to 8 November, collected from a Wednesday, so about 70% observed) the correlations were 0.98 to 0.99. No line moved more than two places, and the top three did not change.

---

## 3. Measured view: Experienced Reliability

**Not published yet.** Every published file carries the measured-view fields as `null` with `measured_status: not_measured`, and the site shows "not measured yet" rather than a number, until the quality gate below passes (D42, D44).

### Inferring arrivals

TfL does not publish when trains actually arrived. Each Arrivals board shows predictions, polled every minute, and an arrival is inferred from how a train's predictions end. The rules, with a Python reference implementation (`src/mind_the_data/analysis/arrivals.py`) that the SQL model is tested against row for row:

1. **A board** is one platform and direction at a station (D28).
2. **Duplicates.** Within one board in one poll, a prediction within 15 s of the one before it is the same train listed twice, and is dropped (D25).
3. **Linking sightings across polls.** Predictions in consecutive successful polls are the same train when they are each other's nearest match and their expected arrivals are within 45 s. A train standing at the platform links by a separate rule. On lines whose vehicle ids are stable, sightings link by vehicle id instead; today that is the Victoria line only (D26, D27).
4. **Arrival.** A train has arrived when its last sighting was at most 120 s from the platform and the next successful poll no longer shows it, or when it shows 0 s at the last poll. Time to go is computed from the poll time, not TfL's `time_to_station`, which counts from an older timestamp (D40).
5. **Arrival time** is the last expected arrival seen. Two arrivals on one board within 45 s are one train that dropped off the board for a poll (D26).
6. **Phantoms**, trains seen once and never within 120 s, are discarded.
7. **A failed poll** neither confirms nor denies an arrival (D28).

### The timetable

Scheduled headways come from TfL's weekly timetables for each measurement station and direction, with platforms pooled because timetables have none (D29). Every listed departure counts, including two in the same minute (D31). Each day uses the latest timetable fetched by then (D32).

TfL returns no timetable for the Elizabeth line or the Overground, so those lines use estimated frequencies per station, hour band and day type (`seed_line_frequencies`, flagged `is_fallback`). Where two estimates disagreed, the lower frequency was used, so service is never judged against a busier timetable than the real one (D33).

### Headways and excess wait

A **headway** is the time between consecutive inferred arrivals at one station and direction, platforms pooled (D29). It belongs to the London hour of the arrival that ends it, the train a waiting passenger boards (D30). A headway that spans a collection gap, or an hour without scheduled service, is dropped rather than read as a long gap (D28, D42).

For each station-direction-hour, assuming passengers arrive at random:

```
actual average wait    = Σ h² / (2 × Σ h)   over observed headways h
scheduled average wait = Σ H² / (2 × Σ H)   over scheduled headways H
excess wait            = actual − scheduled   (minutes; negative values kept)
```

A negative excess wait means the line ran better than its timetable, and is shown as such.

The weekly **excess wait** for a line is the mean over its gated hours that have at least one headway (D42).

### Long gaps

A **long gap** is a headway more than twice the longest scheduled gap in that hour (D34). Using the longest rather than the mean scheduled gap flags fewer gaps: a gap counts only when no scheduled gap that hour comes near it. Long gaps are reported per 100 gated station-direction-hours (D42).

### Quality gate

An hour of inferred arrivals is only trusted when the inference agrees with what the boards showed (ADR-014, proposed, D46). For each line, station, direction and London hour (D42):

- the hour must be **observed**: at least 54 successful polls at the station, no gap over 150 s between polls, and a single UTC hour (the repeated hour at the October clock change is left out);
- trains **on the boards** are counted by vehicle id: each visit of an id to the station and direction that came within 120 s of it, plus inferred arrivals whose train had no id. An hour with no train on the boards and none inferred is not judged; one with inferred trains but none on the boards fails;
- inferred trains must be **between 80% and 120%** of the trains on the boards.

The plan first compared inferred trains with the timetable. The boards list only about 85% of timetabled trains, while the inference finds almost exactly the trains the boards list, so that band failed hours where the inference was right ([gate recheck](data-quality/2026-11-12-gate-recheck.md)). The band's width is kept: at 5 trains an hour, ±20% is one train.

Hours outside the band are excluded from excess wait, long gaps and the Honesty Gap. A line is ranked on the measured view only when at least half its scheduled station-direction-hours pass the gate (D42).

**Current result.** On 9 to 13 November 87.1% of judged core-station hours on measured Tube lines were in band, against a target of 80% ([data-quality/2026-11-14-fidelity-gate.md](data-quality/2026-11-14-fidelity-gate.md)); under the old band the same hours scored 65.4%. Failing hours are almost all above 120%: on lines that do not link by vehicle id, a train held short of the platform can count once per poll (D26, D27). The gate test still warns rather than fails (D44), and the measured view is still not published (D42).

### Trains run against the timetable

Trains on the boards divided by scheduled trains, over observed station-direction-hours: **0.85** across the measured Tube lines on 9 to 13 November, from 0.72 (District) to 0.92 (Bakerloo). The boards may leave trains out, so this is a lower bound on what ran, not a count of cancellations. On the shared section of the Circle and Hammersmith & City lines the boards file every westbound train under Hammersmith & City, so both lines' figures are withheld until trains there can be assigned by vehicle-id history (ADR-014). Like the rest of the measured view it is not published yet (D42).

### Lines not measured

Four lines are marked `not_measured`, with no measured numbers or rank: the DLR (no vehicle ids, predictions rounded to the minute), the tram (closures and directions with no timetable), Waterloo & City (both its stations are termini) and the Suffragette line (closed at both its stations during the review) (D43). Tube termini in the direction trains end, and three tram station-directions TfL returns no timetable for, are unscheduled and so unmeasured (D33).

### The Honesty Gap

An hour counts towards the Honesty Gap when the line's worst declared status was **Good Service for the whole hour** and at least one gated measurement in that hour recorded a **long gap**. It is reported as hours per week and as a share of the line's Good Service hours with at least one gated measurement (D42).

Only the hour in which the long gap ends is checked for Good Service. A gap that began during declared delays in the previous hour still counts if the hour it ends in was all Good Service, so the Honesty Gap reads slightly high; requiring Good Service over the gap's whole span is the alternative under consideration (D42).

---

## 4. What is published

Every Monday the latest complete week is exported as JSON and two share card images (PLAN 8, [site-integration.md](site-integration.md)):

- `league/{iso_week}.json` and `league/latest.json`: the ranked table, provisional flags and unranked reasons, the week's highlights (Wooden Spoon, Line of the Week, worst incident), the Monday Morning Index, the Engineering Works and Recovery Time tables, the week's industrial action, the strikes-excluded table, and one insight line;
- `lines/{line_id}.json`: each line's weekly history, with unranked reasons and hours under works, and typical bad hours;
- `divisions/{yyyy-mm}.json`: the month's divisions, or the seeding, or "not yet seeded" (D56, D57);
- `season/{season}.json`: the season table to date (D58);
- `meta/methodology.json`: the weights, thresholds and open decisions actually used, from the same seeds as the build, plus the industrial action list with sources and the sensitivity analysis;
- `meta/index.json`: every published week, month, season and line file;
- `cards/{iso_week}.png`: 1200×675 and 1080×1350 share cards.

Every file carries `schema_version`, `generated_at`, `data_window`, `completeness` and `git_sha`, so any number can be traced to the code that made it. Each run rewrites every past week from the current marts, so the archive always shows the current methodology (D39).

The card's insight line names the week's biggest Honesty Gap once the measured view is published; until then, the lowest Good Service % among lines that are not provisional (D38).

---

## 5. Known limits

- **One-minute resolution.** A disruption shorter than a minute between polls can be missed, and every status is held for the whole minute it was seen.
- **Declared status is TfL's word.** The declared view measures what TfL said, not what ran. That is the point of the measured view.
- **Flat weights for partial suspensions.** A Part Suspended line scores 5 whether one station or half the line is shut. Scaling by the affected section is planned for later.
- **Long closures distort small denominators.** A line under engineering works is scored on the hours left, where delays on top of a part closure count while the Good Service around them does not. Over half its hours closed, it is not ranked (D22); a quarter to a half, it is marked provisional (D35).
- **No passenger weighting.** A minute on the Central line counts the same as a minute on the Waterloo & City.
- **Estimated hours and frequencies.** Seven lines' service hours (D17) and the Elizabeth line and Overground frequencies (D33) are estimates. For those lines the gate compares boards with estimates partly checked against the same boards, so a pass is weak evidence.
- **Held trains.** Without vehicle-id linking, a train held just outside a platform can count once per poll, which shortens headways exactly when there is a delay. This biases the measured view against showing delays (D26). It is most of what the gate now catches.
- **Boards are not a train register.** Trains run is what the boards listed; a train the boards never showed counts as not run.
- **Circle and Hammersmith & City share boards that misfile trains** on the section from Baker Street to Liverpool Street, which distorts both lines' measured numbers there (ADR-014).
- **Platform changes.** A train that switches platform during its approach can count at both (D28).
- **Strikes** are kept in the main table. The strikes-excluded table drops whole days, so a noon-to-noon strike also drops the unaffected half of each day (D60). The seed is maintained by hand from public sources; an announced strike is updated once its date passes.
- **Divisions seeded on two weeks.** One unusual week can put a line in the wrong division for a month; the seeding is marked provisional until four weeks exist (ADR-015).
- **No external validation yet.** Once three 4-week periods exist, the ranking will be compared with TfL's own share of scheduled trips operated, and the rank correlation reported here.

---

## 6. Decisions behind this methodology

ADR-004 is accepted, and D19, D22 and D36 were decided by the owner on 14 November 2026. Every other entry is implemented as described and is waiting for a final decision, so it is **provisional** and may change. A change is a seed or variable edit and a rebuild; past weeks are rebuilt with it (D39).

| Decision | Subject | Affects |
| --- | --- | --- |
| [ADR-004](adr/004-multiple-statuses.md) | Highest weight wins when statuses overlap | declared (accepted) |
| [ADR-013](adr/013-poll-per-line-minute.md), D23 | Which poll and entries describe a minute | declared |
| D16 | Service Closed is left out of the score | declared |
| D17 | Scheduled hours; estimates for 7 lines | both |
| D18 | Incidents and recovery | declared |
| D19 | Disruption Index: time-weighted mean severity, 0 to 800 | declared (decided) |
| D20 | Ties, unranked and provisional lines | declared |
| D21 | Time bands follow the London calendar day | declared |
| D22 | Ranked only if open at least half the scheduled hours | declared (decided) |
| D25 | Duplicate predictions (15 s) | measured |
| D26, D40 | Linking sightings, arrival rule, time to go | measured |
| D27 | Vehicle-id linking (Victoria only) | measured |
| D28, D29 | Boards, failed polls, pooled platforms | measured |
| D30, D31, D32 | Hours of a gap, same-minute departures, timetable versions | measured |
| D33 | Fallback frequencies (Elizabeth line, Overground) | measured |
| D34 | Long gap against the longest scheduled gap | measured |
| D35 | Provisional when 25% or more is closures | declared, publishing |
| D36 | Publish with provisional labels while decisions are open | publishing (decided) |
| D37 | Typical bad hours | publishing |
| D38 | The card's insight line | publishing |
| D39 | Past weeks rewritten each run | publishing |
| D42 | Gate grain, denominators, Honesty Gap | measured |
| D43 | Lines not measured | measured |
| D44 | Gate test warns until the gate passes | measured |
| [ADR-014](adr/014-gate-tests-inference-fidelity.md), D46 | Gate tests inference against trains on the boards; trains run reported | measured |
| D47 | Line of the Week | declared, publishing |
| D48 | Wooden Spoon and worst incident | declared, publishing |
| D49 | Monday Morning Index | declared, publishing |
| D50 | Engineering Works table per week | declared, publishing |
| [ADR-015](adr/015-divisions-seed-from-two-weeks.md), D56 | Divisions seeded from two weeks, provisional | declared, publishing |
| D57 | Division size, monthly measure, promotion and relegation | declared, publishing |
| D58 | Season table, April to March | declared, publishing |
| D59 | Recovery Time table (provisional on D18) | declared, publishing |
| D60 | Industrial action seed and strikes-excluded table | declared, publishing |
| D61 | Sensitivity weight sets | declared, publishing |

The weights for the status codes the plan names, and the time-band weights, are the build plan's values. D16 adds Service Closed to the weights, and D21 decides how the bands map onto calendar days. Sensitivity under three alternative weight sets is reported each week in `meta/methodology.json` (see Sensitivity above, D61).

Powered by TfL Open Data.
