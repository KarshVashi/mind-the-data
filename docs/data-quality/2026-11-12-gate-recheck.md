# M4 gate recheck, 9 to 12 November

Date: 2026-11-13. Data: dev. Staging was backfilled for 9 to 13 November, one day past the window, because an arrival at the end of the input is provisional until a later poll confirms it (D28). Then `int_inferred_arrivals`, `int_headways` and `int_measured_hours` were rebuilt in full, from 1 November. This page asks two things. Does the gate still stand where the [measured-view page](2026-11-09-measured-view.md) left it? And does vehicle-id linking on more Tube lines (D27) lift it?

Short answer: the gate is at 65.9% against the 80% target. Linking by vehicle id on the eight lines whose ids pass the checks lowers it to 63.9%, so the seed stays at Victoria only. Most hours fail because the boards list fewer trains than the timetable, not because the inference loses trains. That replaces the explanation on the 9 November page, which put the shortfall down to lines that do not link by vehicle id. On the Circle and Hammersmith & City lines, the boards also file trains under the wrong line.

## Method

The gate figure is the M4 test's measure (`assert_measured_hours_pass_the_gate`). It is the share of observed core-station hours on measured Tube lines whose inferred trains are 80% to 120% of the timetable. The window is fixed instead of the test's last seven days:

- **Window:** Mon 9 to Thu 12 November, London dates.
- **Weekday peak:** hours starting 07:00 to 09:00 and 16:00 to 18:00, the `time_bands` AM and PM peaks.
- **Comparison:** Thu 5 to Sat 7 November, from the same full rebuild of the intermediate models.
- **Excluded lines:** Waterloo & City is the one Tube line not measured (D43; see the [9 November page](2026-11-09-measured-view.md#excluded-lines)).

Medians come from Athena's `approx_percentile` and can move by 0.01 between runs on the same data.

The vehicle-id checks read `stg_arrivals` and `stg_polls` for 9 to 12 November (about 650 MB scanned in all). `000` and `001` count as no id, as in the model.

## Baseline: Victoria-only linking (current seed)

| Line | Observed hours | In band, 9 to 12 Nov | Weekday peak | Below 80% | Above 120% | Median ratio | In band, 5 to 7 Nov |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Bakerloo | 470 | 88.5% | 89.6% | 3.2% | 8.3% | 1.00 | 71.1% |
| Central | 471 | 84.1% | 86.1% | 13.2% | 2.8% | 0.92 | 80.9% |
| Jubilee | 478 | 74.7% | 75.7% | 18.2% | 7.1% | 0.95 | 88.3% |
| Piccadilly | 318 | 72.6% | 69.8% | 15.4% | 11.9% | 0.95 | 72.3% |
| Northern | 316 | 72.5% | 63.5% | 20.6% | 7.0% | 0.98 | 87.5% |
| Victoria | 635 | 71.7% | 75.0% | 26.6% | 1.7% | 0.86 | 70.7% |
| Metropolitan | 459 | 65.6% | 68.1% | 28.1% | 6.3% | 0.91 | 62.6% |
| Circle | 643 | 52.3% | 52.1% | 42.9% | 4.8% | 0.85 | 48.0% |
| District | 484 | 50.0% | 37.5% | 45.2% | 4.8% | 0.84 | 54.4% |
| Hammersmith & City | 480 | 35.6% | 33.3% | 10.2% | 54.2% | 1.43 | 30.8% |
| **All** | **4,754** | **65.9%** | **64.9%** | 23.6% | 10.5% | 0.94 | **65.6%** (3,693 hours) |

The 5 to 7 November figure matches the earlier page, so the rebuild changed nothing there.

## Vehicle id per Tube line, 9 to 12 November

Three checks:

- **Placeholders:** the share of rows on the line's measurement boards with `000` or `001` (read as no id).
- **Next poll:** take a sighting more than 180 s out at a core station, and look at the station's next successful poll:
  - *same id:* the id is on the same platform board;
  - *swap:* the id is gone, and a different id is within 45 s of the same expected arrival (under linking, one train split in two);
  - *jump:* the same id is on the board with an expected arrival more than 5 minutes away (two trains sharing an id).
- **Along the leg:** a pass is an id's last sighting within 120 s of a station, by direction. Is the same id on the next measurement station's board, for an expected arrival 1 to 40 minutes later? The check does not filter by destination, so only legs that every train in that direction runs through are shown. Legs where trains branch or turn short in between are left out, such as Morden to Bank (the Charing Cross branch), Earl's Court to Victoria eastbound (Edgware Road trains) and Victoria to Barking (Tower Hill terminators).

| Line | `000` / `001` | Same id next poll | Swap | Jump > 5 min | Follows along the leg (passes; p10 to p90 travel) |
| --- | --- | --- | --- | --- | --- |
| Victoria | 0% / 0.10% | 99.89% | 0.05% | 0.05% | 100% on all six core legs (e.g. Oxford Circus to King's Cross, 1,796; 3.1 to 4.5 min) |
| Bakerloo | 0% / 0% | 99.81% | 0.02% | 0.20% | Oxford Circus to Baker Street 100% (1,133; 1.5 to 2.8 min), Waterloo to Elephant & Castle 100% (1,107) |
| Central | 0% / 1.45% | 99.56% | 0.13% | 0.08% | Oxford Circus to Bank 99.5% (1,534), Bank to Stratford 99.6% (1,520), Stratford to Oxford Circus 100% (1,477) |
| Circle | 0% / 0% | 98.15% | 0.20% | 0.21% | Liverpool Street to Victoria 99.1% (429), Victoria to Baker Street 99.5% (433), Baker Street to Victoria 98.9% (438) |
| Hammersmith & City | 0.01% / 0.06% | 98.13% | 0.12% | 0.14% | King's Cross to Liverpool Street 100% (391), Liverpool Street to Baker Street 100% (827), Baker Street to Hammersmith 100% (821) |
| Jubilee | 0.59% / 0% | 99.82% | 0.04% | 0.11% | Baker Street to Green Park 100% (1,530), Green Park to Waterloo 100% (1,528), Waterloo to Baker Street 100% (1,365) |
| Metropolitan | **20.92%** / 0% | 99.34% | 0.03% | 0.38% | Baker Street to Liverpool Street 100% (799), Liverpool Street to King's Cross 100% (657), King's Cross to Baker Street 100% (685) |
| Northern | 0.04% / 1.29% | 99.81% | 0.04% | 0.06% | Bank to Morden 100% (1,439; 27 to 30 min) |
| Piccadilly | 0% / 0.02% | 99.08% | 0.04% | 0.24% | Green Park to King's Cross 100% (1,395), King's Cross to Green Park 99.9% (1,120) |
| District | 0.85% / 0.83% | 98.33% | 0.05% | 0.12% | **Victoria to Earl's Court westbound 78.5%** (1,081; 7.0 to 9.2 min) |

Two trains under one id at once are rare on core boards. The same real id twice on one station-direction board in one poll, more than 2 minutes apart, is under 0.7% of id-polls at every core board with a direction. It is common only where trains turn back: Wimbledon 57%, Elephant & Castle 23%, Barking (Hammersmith & City) 16%. It is also common on the null-direction boards at Barking (District, 27%) and Baker Street (Metropolitan, 27%), which `int_measured_hours` does not score.

Verdict:

| Lines | Verdict |
| --- | --- |
| Victoria (already on) | Safe to link by id, as in week one. |
| Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern, Piccadilly | Safe to link by id. The id stays on the board from poll to poll and follows the train down the line. Metropolitan's `000` has grown from 7% of rows in week one to 21%; those sightings fall back to expected-arrival linking. |
| District | Not yet. Every westbound train from Victoria passes Earl's Court, yet the id follows only 78.5% of them, about evenly across the Ealing Broadway, Richmond and Wimbledon trains. Of the passes not followed, about 60% never show the id at Earl's Court at all. |

## Experiment: linking on the eight safe lines

`seed_vehicle_id_lines` with `link_by_vehicle_id = true` for Bakerloo, Central, Circle, Hammersmith & City, Jubilee, Metropolitan, Northern and Piccadilly as well as Victoria. Then the seed and the three models were rebuilt on dev.

| Line | In band, 9 to 12 Nov | Change | Weekday peak | Below 80% | Above 120% | Median ratio | In band, 5 to 7 Nov |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Bakerloo | 94.0% | +5.5 | 92.4% | 6.0% | 0.0% | 0.94 | 69.2% |
| Victoria | 71.7% | 0 | 75.0% | 26.6% | 1.7% | 0.86 | 70.7% |
| Central | 70.9% | −13.2 | 70.1% | 28.7% | 0.4% | 0.85 | 74.3% |
| Jubilee | 69.9% | −4.8 | 57.6% | 29.9% | 0.2% | 0.86 | 89.8% |
| Northern | 67.7% | −4.8 | 58.3% | 28.2% | 4.1% | 0.91 | 90.3% |
| Piccadilly | 65.7% | −6.9 | 55.2% | 33.0% | 1.3% | 0.85 | 66.4% |
| Metropolitan | 62.5% | −3.1 | 66.0% | 33.8% | 3.7% | 0.85 | 58.6% |
| Circle | 54.7% | +2.4 | 57.3% | 43.5% | 1.7% | 0.84 | 48.4% |
| District (not linked) | 50.0% | 0 | 37.5% | 45.2% | 4.8% | 0.83 | 54.4% |
| Hammersmith & City | 35.6% | 0 | 31.9% | 12.1% | 52.3% | 1.36 | 32.2% |
| **All** | **63.9%** | **−2.0** | **60.8%** | 29.0% | 7.0% | 0.88 | **64.5%** |

Linking does what D26 says it should: it stops a held train counting once per poll, so on the linked lines hours above 120% almost vanish: Bakerloo 8.3% to 0.0%, Central 2.8% to 0.4%, Jubilee 7.1% to 0.2%. Overall they only fall from 10.5% to 7.0%, because most of what remains is Hammersmith & City, whose excess comes from the misfiling described below. But most failing hours were already below 80%, and the double counts had been lifting some of them into the band. With the bias removed, more hours fall below. Only Bakerloo, which over-counted, and Circle gain. The seed was put back to Victoria only, and dev rebuilt with it, which reproduced the baseline.

Platform pooling for Metropolitan and District (D28) was not tried. A train that changes platform mid-approach is counted twice (D28), so pooling can only lower counts, and both lines already count too few trains. Pooling would also have to change the board key in `src/mind_the_data/analysis/arrivals.py` and the parity tests as well as the SQL.

## Why the gate still fails

**The boards list about 85% of the timetable.** On core observed hours, 9 to 12 November, with linking on for the eight lines and Victoria, District unlinked (inferred ÷ scheduled, and the same for vehicle-id passes):

| Line | Scheduled | Inferred | Ratio | Id passes within 120 s | Ratio | Id passes at any distance | Ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Victoria | 17,445 | 14,714 | 0.84 | 14,990 | 0.86 | 15,125 | 0.87 |
| Central | 10,664 | 9,033 | 0.85 | 9,074 | 0.85 | 9,271 | 0.87 |
| Jubilee | 10,320 | 8,810 | 0.85 | 8,777 | 0.85 | 8,984 | 0.87 |
| Piccadilly | 6,045 | 5,171 | 0.86 | 5,078 | 0.84 | 5,137 | 0.85 |
| Northern | 6,002 | 5,298 | 0.88 | 5,211 | 0.87 | 5,393 | 0.90 |
| Metropolitan | 6,119 | 5,204 | 0.85 | 4,787 | 0.78 | 4,844 | 0.79 |
| Bakerloo | 7,278 | 6,831 | 0.94 | 6,784 | 0.93 | 6,840 | 0.94 |
| District | 8,974 | 7,098 | 0.79 | 6,447 | 0.72 | 6,492 | 0.72 |
| Circle | 3,760 | 2,221 | 0.59 | 2,170 | 0.58 | 3,222 | 0.86 |
| Hammersmith & City | 2,772 | 3,810 | 1.37 | 3,701 | 1.34 | 3,739 | 1.35 |
| Circle and H&C together | 6,532 | 6,031 | 0.92 | 5,871 | 0.90 | 6,961 | 1.07 |

The Circle and Hammersmith & City rows are distorted by the misfiling below. Many Circle ids drop off the Circle board while still far out and reappear under Hammersmith & City, so their "any distance" passes count some trains twice.

The inference finds about as many trains as there are distinct ids on the boards. It is not dropping trains that the boards showed. The Victoria line at 08:00 on Tuesday 10 November is an example: the timetable has 36 trains an hour each way, and the boards show 25 to 30. Either fewer trains ran than timetabled, or the boards leave some out. The data cannot tell those apart. With a median of about 0.85, every hour sits close to the 0.8 edge.

**Circle and Hammersmith & City trains are filed under each other.** At Baker Street, King's Cross and Liverpool Street, Circle trains towards Edgware Road and Hammersmith are listed as Hammersmith & City (destination "Hammersmith (H&C Line)"). In that direction the Circle gets no trains, and Hammersmith & City gets twice its timetable: 11 to 12 an hour against 6 at King's Cross on 10 November. That alone keeps both lines below 55%. Together they come to 0.92 of their combined timetable (table above).

## Next work

1. Count the Circle and Hammersmith & City trains at the shared stations by route (destination and direction) rather than by the board's `line_id`, or judge the two together against their combined timetable. The rule must be the same in the reference and the SQL.
2. Find out whether the 15% shortfall is real service or missing board entries. Two ways: compare a day of inferred trains with TfL's published performance for the same period, or check the timetable source for trains that do not run.
3. Owner decision: if the shortfall is real, the 80% to 120% band treats a line running 85% of its timetable as unmeasurable. Measuring reliability then needs a different gate, such as one judged against scheduled trains that some source confirms. That would depart from PLAN 7.2 and the M4 acceptance, so it needs a new decision for the owner, not a change under D44. Settle it before linking more lines.
4. Keep vehicle-id linking for the eight lines ready. They pass the id checks, and linking is the correct count once the gate measures what it should. District stays off until its ids follow trains along the line.
