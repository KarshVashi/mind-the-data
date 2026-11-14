# ADR-015: Seed the divisions from two complete weeks, provisionally

Date: 2026-11-14
Status: proposed (the owner asked for the shorter window on 2026-11-14; acceptance is D56)

## Context

PLAN M6 seeds the two divisions "from the first four weeks". Collection began on Wednesday 4 November 2026, so W45 is short (it is about 70% observed and is not a complete week). The first full week, W46 (9 to 15 November), ends tonight. Four complete weeks (W46 to W49) would end on Sunday 6 December, and the first division month would be January 2027. The owner wants time-based requirements shortened so the divisions start sooner.

Two weeks is a small sample. One bad week (a strike, a long closure, a signal failure) can put a line in the wrong division for a month. Three lines move each month, so a mis-seeded line can correct within one or two months.

## Decision

1. **Seed from the first `division_seeding_weeks` complete weeks, default 2** (W46 and W47). A complete week is one every line has finished, with at least `provisional_completeness` (90%) of the network's scheduled minutes observed: the rule D47 already uses to compare weeks.
2. **The order** is the Disruption Index over the seeding weeks, recomputed from summed minutes (D19), ties to the higher Good Service %, then line id. Lines that cannot be ranked over those weeks (D20, D22) are seeded last. The first `division_one_size` lines (10) form Division One.
3. **Provisional label.** While fewer than `division_full_seeding_weeks` (4) complete weeks exist, `rpt_division_seeding.seeding_status` and `seeding.status` in `divisions/{yyyy-mm}.json` say `provisional`; after that, `confirmed`.
4. **Before seeding, say so.** Until enough complete weeks exist, `rpt_division_seeding` and `rpt_divisions_monthly` are empty and the current month's `divisions/{yyyy-mm}.json` has `status: "not_seeded"`, no seeding and no divisions. No table is ever shown that the data does not support.
5. **Divisions play from the first calendar month that starts after the seeding window ends.** Seeding from W46 and W47 ends on 22 November, so December 2026 is the first division month, and the first promotion and relegation is at the end of December. November's file shows the seeding (`status: "seeded"`) once W47 is complete.
6. **The window is a variable.** Setting `division_seeding_weeks: 4` returns to the plan: the seeding then uses W46 to W49 and the first division month becomes January 2027. Every mart rebuilds in full (ADR-012), so the change takes effect on the next build and rewrites every published month (D39).

## Consequences

- Divisions start a month earlier (December, not January).
- The seeding rests on half the data the plan asked for, which is why it is labelled provisional until four weeks exist. When W49 completes the label changes to `confirmed` without re-seeding. If the owner prefers to re-seed from four weeks at that point, set the variable to 4 before the first division month ends.
- A strike in a seeding week counts in full: the seeding uses the main league, not the strikes-excluded variant (D60). The RMT Tube strike announced for 26 and 27 November falls in W48, after the seeding window.

## Alternatives

- **Keep four weeks** (the plan): divisions start in January 2027.
- **Seed from one week**: December too, but on a single week.
- **Re-seed automatically at four weeks**: the December table would change membership mid-month, which is harder to explain than a label.
