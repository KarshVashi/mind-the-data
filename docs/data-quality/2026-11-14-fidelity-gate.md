# M4 gate under ADR-014, 9 to 13 November

Date: 2026-11-14. Data: dev. Staging was backfilled for 9 to 14 November, then every model was rebuilt in full (`dbt build --target dev`, all tests passing). Settings unchanged from the [recheck](2026-11-12-gate-recheck.md): vehicle-id linking on Victoria only (D27), DLR, tram, Waterloo & City and Suffragette not measured (D43).

The gate now compares inferred trains with the trains observed on the boards (vehicle-id passes plus inferred arrivals without an id), not with the timetable ([ADR-014](../adr/014-gate-tests-inference-fidelity.md)). Trains observed over scheduled is reported beside it as trains run.

## Result

Core-station hours on measured Tube lines, Monday 9 to Friday 13 November (London dates), observed hours only. "Judged" hours had at least one train on the boards or at least one inferred; an hour with inferred trains and none on the boards fails (one Piccadilly hour). Weekday peak is the hours starting 07:00 to 09:00 and 16:00 to 18:00.

| Line | Observed hours | Judged | No train on the boards | In band | Weekday peak in band | Above 120% | Median inferred ÷ observed | Trains run | Old gate (timetable band) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Victoria | 795 | 792 | 3 | 97.7% | 99.2% | 2.0% | 1.00 | 0.865 | 73.2% |
| Circle | 804 | 499 | 305 | 93.2% | 86.0% | 6.8% | 1.00 | (0.578, withheld) | 51.5% |
| Central | 590 | 585 | 5 | 91.8% | 95.0% | 8.2% | 1.04 | 0.856 | 81.7% |
| Northern | 396 | 388 | 8 | 91.2% | 84.0% | 8.8% | 1.05 | 0.881 | 72.5% |
| Jubilee | 598 | 597 | 1 | 86.4% | 86.7% | 13.6% | 1.06 | 0.845 | 73.6% |
| Hammersmith & City | 600 | 600 | 0 | 86.0% | 78.3% | 14.0% | 1.00 | (1.330, withheld) | 35.5% |
| Bakerloo | 589 | 588 | 1 | 85.0% | 83.9% | 15.0% | 1.06 | 0.921 | 87.3% |
| District | 606 | 601 | 5 | 84.5% | 82.8% | 15.5% | 1.03 | 0.723 | 47.9% |
| Metropolitan | 574 | 572 | 2 | 75.5% | 75.6% | 24.5% | 1.04 | 0.791 | 67.2% |
| Piccadilly | 398 | 398 | 0 | 74.1% | 82.5% | 25.4% | 1.10 | 0.841 | 71.6% |
| **All** | **5,950** | **5,620** | **330** | **87.1%** | **86.0%** | 12.8% | 1.00 | 0.847 | 65.4% |

Below 80% is 0.1% overall (Victoria and Piccadilly 0.3%, every other line 0.0%). Medians come from Athena's `approx_percentile`.

Other windows, same build:

| Window | Judged hours | In band | Old gate |
| --- | --- | --- | --- |
| Thu 5 to Sat 7 Nov | 3,476 | 87.8% | 65.6% |
| Sat 7 to Fri 13 Nov (the test's last seven days) | 8,021 | 87.3% | 65.1% |

## Trains without an id, and the other lines

Trains with no vehicle id count the same on both sides of the ratio, so where most trains lack one the gate would pass by construction. That does not arise: on 9 to 13 November inferred arrivals without an id are at most 1.7% on any measured line (Jubilee), and 0.0% on every Overground and Elizabeth line line. The gate is still loose away from the core Tube stations, so the acceptance figure is core Tube only, as the plan asks. All stations, every measured line:

| Line | Judged hours | In band | No-id share |
| --- | --- | --- | --- |
| Victoria | 792 | 97.7% | 0.1% |
| Circle | 499 | 93.2% | 0.0% |
| Jubilee | 696 | 80.9% | 1.7% |
| Elizabeth line | 629 | 78.5% | 0.0% |
| Metropolitan | 633 | 75.0% | 0.8% |
| Bakerloo | 687 | 73.8% | 0.0% |
| Hammersmith & City | 794 | 73.6% | 0.0% |
| District | 714 | 72.4% | 1.2% |
| Central | 793 | 71.4% | 1.2% |
| Weaver | 446 | 70.2% | 0.0% |
| Piccadilly | 692 | 66.8% | 0.0% |
| Northern | 684 | 58.0% | 1.2% |
| Mildmay | 525 | 51.8% | 0.0% |
| Windrush | 441 | 39.7% | 0.0% |
| Lioness | 360 | 22.8% | 0.0% |
| Liberty | 160 | 8.1% | 0.0% |

Terminus boards over-count (D33), so lines measured partly at termini fall well below their core figure; the Overground lines are lowest. Hours outside the band leave the measured numbers, so this lowers coverage rather than accuracy.
| Mon 9 to Fri 13 Nov | 5,620 | 87.1% | 65.4% |

**The M4 acceptance is met on the ADR-014 definition**: at least 80% of core-station Tube hours are in band over more than three full days. `assert_measured_hours_pass_the_gate` passes (12.6% out of band against its 30% limit). It still runs at `warn` (D44); switching it to `error` is the owner's call.

## Reading it

- **Failures are the held-train double count.** Almost every failing hour is above 120%, on lines that do not link by vehicle id: a train held short of the platform counts once per poll (D26). Victoria, the one linked line, is 97.7% in band. Piccadilly and Metropolitan, the two lines under 80%, have the most hours above 120%. The recheck found ids stable on both, and linking would remove most of these (D27).
- **Circle's 305 hours with no train on the boards** are its westbound hours at Baker Street, King's Cross and Liverpool Street, where the boards file every westbound train under Hammersmith & City. They are not judged. Both lines' trains-run shares are withheld for the same reason (ADR-014, decision 4).
- **Trains run is 0.85 of the timetable across the Tube lines**, 0.72 on the District. Whether the rest did not run or were not listed cannot be told from the boards (recheck, "Why the gate still fails").

## Threshold

Distribution of inferred ÷ observed over the same judged hours:

| Hours | Judged | ±10% | ±15% | ±20% | ±25% | p5 | p95 | Median trains on the boards |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Victoria (linked) | 792 | 91.4% | 96.5% | 97.7% | 98.2% | 0.90 | 1.05 | 25 |
| Other lines | 4,828 | 66.2% | 75.2% | 85.4% | 89.9% | 1.00 | 1.36 | 14 |
| All | 5,620 | 69.8% | 78.2% | 87.1% | 91.1% | 0.95 | 1.35 | 15 |

One judged hour in ten has 5 or fewer trains on the boards (388 have under 5; 74 of those fail on one train). ADR-014 explains the choice of ±20%.

## Queries

The figures come from `int_measured_hours` (`is_gate_judged`, `fidelity_ratio`, `trains_run_ratio`, `arrival_ratio`) joined to `lines` for the mode, filtered to `role = 'core'`, `is_line_measured` and the window, grouped by line. Each scanned under 1 MB.
