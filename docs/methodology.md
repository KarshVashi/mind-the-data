# Methodology

How the Tube League Table turns Transport for London's live data into a weekly ranking of London rail lines, what each number means, and where the numbers are weak.

The table ranks every line two ways:

- **Declared view, the Disruption Index:** what TfL's own status feed said about the line, minute by minute, weighted by severity and time of day.
- **Measured view, Experienced Reliability:** how long passengers actually waited, inferred from the arrival boards at a fixed set of stations, against the timetable.

The gap between the two, hours in which TfL said Good Service while the boards showed a long gap, is the **Honesty Gap**.

**Status, methodology v0.3.** Both views are built and run daily, and the weekly files carry both. On 14 November 2026 the owner settled: the Disruption Index is a time-weighted mean severity on a 0 to 800 scale (D19); a line is ranked only if it ran at least half its scheduled hours (D22); publishing goes ahead while other definitions are open, with provisional labels (D36); the quality gate tests the inference against the trains on the boards (ADR-014, D46) and stops the build when it fails (D44); the measured view is **published** for each line-week whose own gate passes (D42); and the weekly highlights (D47 to D50). Public publishing starts once the Cloudflare setup is done. Most definitions below are readings of an ambiguous rule and are waiting for a final decision. Each is tagged with its decision number (`D16`) from [DECISIONS.md](DECISIONS.md) or its record (`ADR-004`) in [adr/](adr/), and the table at the end says which are provisional. Every published file lists the open decisions it rests on in `open_decisions`.

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

---

## 3. Measured view: Experienced Reliability

**Published since 14 November 2026 (D42),** line-week by line-week. A line's measured numbers appear only when its own week passes the gate (see "What is published for a line-week" below); otherwise the file says why, in `measured_withheld_reason`, and the site shows the reason rather than a number. Experienced Reliability ranks the lines by excess wait. The Disruption Index still leads the card while the measured ranking builds a history; PLAN 7.3 has the measured rank lead once validated.

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

An hour of inferred arrivals is only trusted when the inference agrees with what the boards showed (ADR-014, accepted, D46). For each line, station, direction and London hour (D42):

- the hour must be **observed**: at least 54 successful polls at the station, no gap over 150 s between polls, and a single UTC hour (the repeated hour at the October clock change is left out);
- trains **on the boards** are counted by vehicle id: each visit of an id to the station and direction that came within 120 s of it, plus inferred arrivals whose train had no id. An hour with no train on the boards and none inferred is not judged; one with inferred trains but none on the boards fails;
- inferred trains must be **between 80% and 120%** of the trains on the boards.

The plan first compared inferred trains with the timetable. The boards list only about 85% of timetabled trains, while the inference finds almost exactly the trains the boards list, so that band failed hours where the inference was right ([gate recheck](data-quality/2026-11-12-gate-recheck.md)). The band's width is kept: at 5 trains an hour, ±20% is one train.

Hours outside the band are excluded from excess wait, long gaps and the Honesty Gap.

**Current result.** On 9 to 13 November 87.1% of judged core-station hours on measured Tube lines were in band, against a target of 80% ([data-quality/2026-11-14-fidelity-gate.md](data-quality/2026-11-14-fidelity-gate.md)); under the old band the same hours scored 65.4%. Failing hours are almost all above 120%: on lines that do not link by vehicle id, a train held short of the platform can count once per poll (D26, D27). The gate test now fails the build when more than 30% of the last seven days' judged core-station hours are out of band (D44), so a broken inference stops the daily transform before anything is published.

### What is published for a line-week

The build-wide gate says the inference works; each line-week must also pass on its own before its numbers are published. In order, the measured numbers are withheld when:

| Reason | Rule | Decision |
| --- | --- | --- |
| `line_not_measured` | the line is one of the four not measured | D43 |
| `shared_section_misfiled` | Circle or Hammersmith & City (below) | D52 |
| `no_measured_hours` | no hour passed the gate | D42 |
| `gate_not_judged` | no judged hour at a core or branch station (Liberty has only termini) | D51 |
| `gate_failed` | more than 30% of the line's judged hours at core and branch stations are out of band, the build-wide limit applied to the line | D51 |
| `too_few_measured_hours` | under half its scheduled station-direction-hours measured | D42 |

Termini are left out of the line's gate because their boards over-count arrivals (D33); their hours still drop out of the numbers one by one when they fail. A custom window, such as a Wednesday-to-Tuesday post, sums the daily figures over its dates and applies the same rules to the sums, so a line can pass over the window and fail on one day.

The Elizabeth line and the Overground are measured against estimated frequencies, not TfL timetables (D33). Their numbers are published with `is_weak_evidence: true` and are **not ranked** on Experienced Reliability (D53): estimates were set to the lower of two sources, so their scheduled waits are long and their excess wait reads low. A trains-run share above 100% on these lines is the sign of that.

### Trains run against the timetable

Trains on the boards divided by scheduled trains, over observed station-direction-hours: **0.85** across the measured Tube lines on 9 to 13 November, from 0.72 (District) to 0.92 (Bakerloo). The boards may leave trains out, so this is a lower bound on what ran, not a count of cancellations. On 9 to 13 November the published lines ranged from 0.67 (District) to 0.91 (Bakerloo). On the shared section of the Circle and Hammersmith & City lines the boards file every westbound train under Hammersmith & City, so both lines' figures are withheld until trains there can be assigned by vehicle-id history (ADR-014).

### Lines not measured

Four lines are marked `not_measured`, with no measured numbers or rank: the DLR (no vehicle ids, predictions rounded to the minute), the tram (closures and directions with no timetable), Waterloo & City (both its stations are termini) and the Suffragette line (closed at both its stations during the review) (D43). Tube termini in the direction trains end, and three tram station-directions TfL returns no timetable for, are unscheduled and so unmeasured (D33).

### Circle and Hammersmith & City

Every westbound train at Baker Street, King's Cross and Liverpool Street is listed as Hammersmith & City, Circle trains included. The Hammersmith & City line's westbound headways there are therefore about halved, which makes its excess wait read low and hides its long gaps, while the Circle has no westbound trains at those stations at all. Until trains are split by vehicle-id history (the ADR-014 follow-up), **neither line's measured view is published** (D52). Their declared numbers are unaffected.

### The Honesty Gap

An hour counts towards the Honesty Gap when the line's worst declared status was **Good Service for the whole hour** and at least one gated measurement in that hour recorded a **long gap**. It is reported as hours per week and as a share of the line's Good Service hours with at least one gated measurement (D42).

Only the hour in which the long gap ends is checked for Good Service. A gap that began during declared delays in the previous hour still counts if the hour it ends in was all Good Service, so the Honesty Gap reads slightly high; requiring Good Service over the gap's whole span is the alternative (D42).

**A known risk.** The boards list about 85% of timetabled trains, and whether the rest ran is not known (ADR-014). If they ran, the gaps around them exist only on the boards, so the Honesty Gap reads high. Long gaps are frequent on several Tube lines (about 70 to 125 per 100 measured hours on 9 to 13 November, Bakerloo 17), so the Honesty Gap is large there: the Piccadilly line had long gaps in 78 of its 86 measured Good Service hours. These are published as measured, with this caveat beside them, until the boards can be checked against an independent count.

---

## 4. What is published

Every Monday the latest complete week is exported as JSON and two share card images (PLAN 8, [site-integration.md](site-integration.md)):

- `league/{iso_week}.json` and `league/latest.json`: the ranked table, provisional flags and unranked reasons, the week's highlights (Wooden Spoon, Line of the Week, worst incident), the Monday Morning Index, the Engineering Works table, and one insight line;
- `lines/{line_id}.json`: each line's weekly history, with unranked reasons and hours under works, and typical bad hours;
- `meta/methodology.json`: the weights, thresholds and open decisions actually used, from the same seeds as the build;
- `meta/index.json`: every published week and line file;
- `cards/{iso_week}.png`: 1200×675 and 1080×1350 share cards.

Every file carries `schema_version`, `generated_at`, `data_window`, `completeness` and `git_sha`, so any number can be traced to the code that made it. Each run rewrites every past week from the current marts, so the archive always shows the current methodology (D39).

The card's insight line names, in order: the most Honesty Gap hours; otherwise the lowest trains-run share under 95%; otherwise the lowest Good Service % (D38). It never names a provisional line, or a weak-evidence line for a measured headline. Measured fields in each league and line file are `null` with a `measured_withheld_reason` when withheld; `meta/methodology.json` lists the gate thresholds and the withheld lines.

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
- **Circle and Hammersmith & City share boards that misfile trains** on the section from Baker Street to Liverpool Street, which distorts both lines' measured numbers there, so neither is published (ADR-014, D52).
- **Platform changes.** A train that switches platform during its approach can count at both (D28).
- **Strikes** are kept in. A "strikes excluded" variant is planned (PLAN 7.4).
- **No external validation yet.** Once three 4-week periods exist, the ranking will be compared with TfL's own share of scheduled trips operated, and the rank correlation reported here.

---

## 6. Decisions behind this methodology

ADR-004 and ADR-014 are accepted, and D19, D22, D36, D42, D44, D46 and D47 to D50 were decided by the owner on 14 November 2026. Every other entry is implemented as described and is waiting for a final decision, so it is **provisional** and may change. A change is a seed or variable edit and a rebuild; past weeks are rebuilt with it (D39).

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
| D42 | Gate grain, denominators, Honesty Gap; publish the measured view | measured, publishing (decided) |
| D43 | Lines not measured | measured |
| D44 | Gate test fails the build | measured (decided) |
| [ADR-014](adr/014-gate-tests-inference-fidelity.md), D46 | Gate tests inference against trains on the boards; trains run reported | measured (decided) |
| D47 | Line of the Week | declared, publishing (decided) |
| D48 | Wooden Spoon and worst incident | declared, publishing (decided) |
| D49 | Monday Morning Index | declared, publishing (decided) |
| D50 | Engineering Works table per week | declared, publishing (decided) |
| D51 | A line-week's own gate decides whether its measured numbers are published | measured, publishing |
| D52 | Circle and Hammersmith & City measured view withheld | measured, publishing |
| D53 | Weak-evidence lines shown but not ranked on Experienced Reliability | measured, publishing |

The weights for the status codes the plan names, and the time-band weights, are the build plan's values. D16 adds Service Closed to the weights, and D21 decides how the bands map onto calendar days. Sensitivity checks under alternative weight sets are planned, and will be reported here with their rank correlations.

Powered by TfL Open Data.
