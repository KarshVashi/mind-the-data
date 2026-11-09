# Measured view on dev, first build (M4)

Date: 2026-11-09. Data: dev, 2026-11-04 to 2026-11-08, after the `stg_arrivals` key fix (D45). Models: `int_inferred_arrivals`, `int_headways`, `int_measured_hours`, `fct_line_weekly`. Settings as recommended by the [week-one review](2026-11-08-week-one.md) and pending the owner: vehicle-id linking on Victoria only (D27), time to go from the poll (D40), DLR, tram, Waterloo & City and Suffragette not measured (D43).

## Staging fix

`stg_arrivals` rows on 2026-11-06, before and after the key change (raw: 1,444,816 predictions):

| Line | Before | After |
| --- | --- | --- |
| DLR | 4,749 | 29,154 |
| Metropolitan | 32,247 | 51,953 |
| District | 40,493 | 64,124 |
| Northern | 110,890 | 162,477 |
| Victoria | 54,124 | 54,906 |
| All lines | 1,250,353 | 1,444,004 |

The 812 rows still dropped are the same prediction listed twice in one response.

## SQL against the Python reference

`scripts/check_arrival_parity.py` runs the reference on a whole dev day (with an hour either side) and compares every arrival column by column with `int_inferred_arrivals`.

| Day | Arrivals | Differences |
| --- | --- | --- |
| 2026-11-06 | 53,815 | 0 |
| 2026-11-07 | 50,780 | 0 |

The same comparison runs offline on a 10-minute fixture of real boards plus a synthetic station built on the rules' boundaries (a failed poll, an empty board, a placeholder id, two id candidates, an arrival due before the poll, values exactly on 15 s, 45 s and 120 s): `tests/unit/test_arrival_parity.py` and the dbt unit test `int_inferred_arrivals_matches_the_reference`.

## Victoria re-check

Oxford Circus and King's Cross, Thu 5 to Sat 7 November, London hours 5 to 23, 228 station-direction-hours, all observed:

| Measure | Value | Review, variant C |
| --- | --- | --- |
| Inferred against scheduled trains | 83.9% (5,265 / 6,276) | 83.8% |
| Hours in the 80% to 120% band | 76.3% | 76.3% |
| Weekday peak hours in band | 75.0% | 75.0% |
| Weekday peak ratio | 83.4% | 83.2% |

The SQL model reproduces the review's recommended variant. Across Victoria's core stations the share in band is 70.7%.

## M4 acceptance: not met yet

Core-station hours on measured Tube lines, observed hours only:

| Window | Observed hours | In band |
| --- | --- | --- |
| Thu 5 to Sat 7 Nov | 3,693 | 65.6% |
| Mon 2 to Sun 8 Nov | 6,057 | 66.2% |

Per line, 5 to 7 November, core stations: Jubilee 88.3%, Northern 87.5%, Central 80.9%, Piccadilly 72.3%, Bakerloo 71.1%, Victoria 70.7%, Metropolitan 62.6%, District 54.4%, Circle 48.0%, Hammersmith & City 30.8%. The target is 80%. `assert_measured_hours_pass_the_gate` reports this as a warning (D44).

The remaining shortfall is mostly undercounting on lines that do not yet link by vehicle id, and the Circle, Hammersmith & City and Metropolitan boards, which share platforms and do not line up with their timetables. Next steps: the Monday 9 November check for the other Tube lines (D27), then re-run this page.

## Excluded lines

DLR, tram, Waterloo & City and Suffragette: `not_measured` in `fct_line_weekly` (D43). The Elizabeth line and Overground are measured against fallback frequencies (D33); a pass there is weak evidence.

## Caution on long gaps and the Honesty Gap

A train the inference misses joins two gaps into one. With the gate at 80% to 90% of the timetable, long gaps read high: 19 to 99 per 100 measured hours on Tube lines for the week of 2 November, and the Honesty Gap at 35% to 81% of Good Service hours. Excess wait is plausible (Tube lines 0.5 to 1.2 minutes). None of these are results until the gate passes (D42).
