# ADR-014: The quality gate tests inference fidelity; trains run is its own measure

Date: 2026-11-14
Status: accepted (owner, 2026-11-14, D46). Follow-ups settled the same day: the gate test runs at `error` (D44) and the measured view is published per line-week (D42, D51); Circle and Hammersmith & City stay withheld until the split in decision 4 (D52). Amended the same day by the owner: a gate failure no longer holds back the declared view (D55, "Gate failures and the declared view" below), and the Honesty Gap stays off the share card until open item 2 is answered (D54).

## Context

PLAN 7.2 trusts an hour of inferred arrivals only when inferred trains are 80% to 120% of the **timetable**, and the M4 acceptance asks for 80% of core-station Tube hours in that band. The gate has sat at about 66% since the first build ([measured view](../data-quality/2026-11-09-measured-view.md)).

The [9 to 12 November recheck](../data-quality/2026-11-12-gate-recheck.md) found why:

- **The boards list about 85% of timetabled trains.** Counting distinct vehicle ids that come within 120 s of each core station gives 0.84 to 0.88 of the timetable on Victoria, Central, Jubilee, Piccadilly and Northern, 0.78 on the Metropolitan and 0.72 on the District.
- **The inference finds what the boards show.** Inferred arrivals match those vehicle-id passes almost exactly: Victoria 14,714 against 14,990, Central 9,033 against 9,074, Jubilee 8,810 against 8,777 (linking by id on the eight lines that pass the id checks).
- So every hour sits near the 0.8 edge of the band, and **the gate cannot tell trains not run (or not listed) from trains the inference lost.** It fails hours where the inference is right and passes hours where two errors cancel.
- **Circle and Hammersmith & City misfile.** At Baker Street, King's Cross and Liverpool Street, every westbound train is listed as Hammersmith & City with the destination "Hammersmith (H&C Line)", Circle trains included. The Circle shows no westbound trains there, and Hammersmith & City about twice its timetable.

## Decision

1. **The gate tests inference fidelity.** For each line, station, direction and London hour (the D42 grain), compare inferred arrivals with the **trains observed on the boards**:
   - `observed_trains` = vehicle-id passes + inferred arrivals whose train carried no vehicle id;
   - a **vehicle-id pass** (`int_vehicle_passes`) is a visit of one vehicle id to the station and direction (sightings no more than 600 s apart, any platform) that came within 120 s of the station, timed at its last sighting's expected arrival, the time the inference gives the same train. It uses no expected-arrival linking, so it is independent of the D26 and D27 rules;
   - trains with no id (placeholders `000` and `001`, the DLR) have no independent count, so the inference's own count is the best available and they count the same on both sides. That would make the gate pass by construction where most trains lack an id; on 9 to 13 November no measured line has more than 1.7% of inferred arrivals without one, and the DLR is not measured (D43). If a line's share rises, its hours should leave the judged set;
   - this applies on every line, whether or not it links by vehicle id: the ids are on the boards either way.
2. **The band stays 80% to 120%,** now of `observed_trains`. An hour is judged when it is observed (D42) and at least one train was on the boards or inferred. An hour with neither has nothing to judge and is left out of the gate figure; an hour with inferred trains and none on the boards fails. The M4 test still fails when more than 30% of judged core-station hours on measured Tube lines fall outside the band.
3. **Trains run against the timetable** (`observed_trains ÷ scheduled trains`) becomes a measured output of its own: `trains_run_ratio` per hour in `int_measured_hours`, `trains_run_share` per line-week in `fct_line_weekly` (since D55, `fct_line_weekly_measured`), and `measured.trains_run_share` in the contract. The boards may omit trains, so it is a lower bound on what ran, and is labelled so. Like the rest of the measured view it stays `null` in every published file until the owner settles D42.
4. **Circle and Hammersmith & City: follow-up, not built here.** The brief was to assign trains on the shared section by destination. The boards make that impossible: a westbound Circle train and a Hammersmith & City train both show "Hammersmith (H&C Line)", since the Circle runs Edgware Road to Hammersmith in that direction. Separating them needs the vehicle id's history (an id seen on a Circle-only board, Victoria, earlier on the same trip), which is cross-station state in both the Python reference and the SQL, with new parity fixtures. That is too large for this change. Until then:
   - the gate is unaffected, since the misfiled trains are on both sides of the fidelity ratio, and the Circle's westbound hours at the three stations have no train on the boards, so they are not judged;
   - `fct_line_weekly` withholds `trains_run_share` for both lines (var `trains_run_withheld_lines`), because each line's share is wrong in opposite directions;
   - excess wait and long gaps for the two lines stay distorted (Hammersmith & City's westbound headways halve, the Circle's are missing), which is one more reason the measured view is not published.
5. **Linking stays as it is.** Vehicle-id linking remains Victoria only, pending D27. The recheck found ids stable on Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern and Piccadilly (98% to 99.9% of sightings keep their id to the next poll, and 99% to 100% of passes are followed down the line), and linking them brought inferred trains to within 1% to 2% of the id passes. District stays unlinked: its ids follow only 78.5% of westbound trains from Victoria to Earl's Court.

### Why 80% to 120%

From the rebuilt `int_measured_hours`, core-station hours on measured Tube lines, 9 to 13 November ([data-quality page](../data-quality/2026-11-14-fidelity-gate.md)):

| Hours | Judged | ±10% | ±15% | ±20% | ±25% | p5 to p95 of inferred ÷ observed |
| --- | --- | --- | --- | --- | --- | --- |
| Victoria (linked by id) | 792 | 91.4% | 96.5% | 97.7% | 98.2% | 0.90 to 1.05 |
| Other lines (not linked) | 4,828 | 66.2% | 75.2% | 85.4% | 89.9% | 1.00 to 1.36 |

- On the Victoria line the inference and the passes follow the same ids, so their remaining disagreement is noise: a train placed in the neighbouring hour, a flicker merged on one side only. At ±10% one good hour in eleven still fails.
- One judged hour in ten has 5 or fewer trains on the boards, where one train is 20% or more. ±20% is the narrowest band that tolerates a one-train difference at 5 trains an hour. Below that (388 hours with under 5 trains, 7% of those judged) one train still fails the hour; 74 of them do.
- On the unlinked lines almost every failing hour is above 120%: the held-train double count of D26, which is exactly what the gate should catch. Hours below 80% are 0.1% overall.
- It keeps the plan's tolerance, applied to what the plan's tolerance was for: the inference's error.

## Alternatives considered

- **Keep the timetable band and lower it to 70%.** It would pass more hours but still mix trains not run with lost trains, and would pass hours where an undercount and missing trains cancel.
- **Judge Circle and Hammersmith & City together against their combined timetable.** Fixes trains run for the pair but not headways, and needs a shared line in every downstream table. Kept as an option for the follow-up.
- **Count observed trains only on lines that link by id.** On unlinked lines the gate would then be the timetable band again, or nothing; the ids are on the boards on every Tube line, so counting them costs nothing.
- **A ±10% band.** Fails one in eleven hours on the best-linked line; too tight for hourly counts of 5 to 36 trains.

## Consequences

- On 9 to 13 November **87.1% of judged core-station Tube hours pass** (5,620 hours), against 65.4% under the timetable band on the same hours. Over the test's own window (7 to 13 November) 87.3%; over 5 to 7 November 87.8% (the old figure there was 65.6%). The M4 acceptance's 80% is met on this definition, so D44's condition is met; the owner then set the test to `error` (D44).
- Two lines are under 80% on their own: Piccadilly 74.1% and Metropolitan 75.5%, both from hours above 120% (held trains counted twice). Linking them by id (D27) is the fix the recheck points to.
- A suspension no longer drops out of the measured view merely by running fewer trains than the timetable: if the boards and the inference agree on how few trains ran, the hour counts, and its long gaps reach the Honesty Gap. D42's note that "the gate's exclusion takes the worst hours out" no longer holds.
- **A risk for the Honesty Gap.** If the boards leave out trains that did run (the recheck could not tell, its open item 2), hours now pass the gate while their headways hold gaps that exist only on the boards. Those long gaps would feed the Honesty Gap and read as TfL understating disruption. Answering whether the 15% shortfall is real service should come before the measured view is published (D42). The view was published with this stated beside the Honesty Gap; the share card's insight line uses neither Honesty Gap hours nor long gaps until it is answered (D54).
- `int_vehicle_passes` reads six columns of `stg_arrivals` over all history and rebuilds in full, like `int_inferred_arrivals` (D41), so its scan grows with the same columns' history and falls under the same M7 revisit.
- This amends PLAN 7.2 (the gate's denominator) and the M4 acceptance wording. PLAN 7.2 should read "inferred trains per hour must fall between 80% and 120% of the trains observed on the boards", with trains run against the timetable reported beside it.
- Revisit when the Circle and Hammersmith & City follow-up lands, and when D27 extends linking.

## Gate failures and the declared view

Added 2026-11-14 at the owner's direction (D55).

With the gate test at `error`, `dbt build` skips every model that descends from everything the test reads (`int_measured_hours` and the lines seed). While `fct_line_weekly` held the measured columns, that was the whole declared side too: `fct_line_weekly` and every report built on it (highlights, Monday Morning, Engineering Works, Recovery Time, divisions, season, strikes excluded, status mix). A failing inference check would have frozen the declared league, which does not use the inference at all.

The measured columns now live in their own model:

- `fct_line_weekly` is the declared view only and reads nothing on the measured path;
- `fct_line_weekly_measured` holds the measured view per line-week, reading `fct_line_weekly` for the line-weeks and `fct_line_measured_daily` for the sums;
- the export joins the two (`marts.py`, the `weekly` query), so the published files keep their shape.

When the gate fails, dbt still exits non-zero, so the run is red and the failure is visible, but it skips only `fct_line_measured_daily` and `fct_line_weekly_measured`. The declared tables and the league, divisions and season are rebuilt as usual. The skipped measured tables keep their last good build. Both weekly tables carry `built_at`, and the measured one `built_london_date`. The export keeps a week's measured numbers only when the measured table was built with the declared one, or when the week had ended before its last build. Otherwise every measured line's numbers are withheld with `measured_withheld_reason: measured_not_rebuilt`, so a stale table never shows a partial week. A custom window (`post_snapshot.py`) follows the same rule.

Per line-week withholding (D51) stays the main guard on what is published. The build-wide gate test is the alarm. `tests/unit/test_dbt_gate_isolation.py` reads the refs in the dbt project and fails if any declared output descends from the measured path, or if the gate test would block one under dbt's rule.

Full decoupling is possible here, because the declared view never needed the measured one. The cost is one more model and a join at export time. One edge remains: if the measured table has never been built (a fresh database whose first build fails the gate), the export's join has no table to read and the export fails. The declared build is unaffected, and the first passing build fixes it.
