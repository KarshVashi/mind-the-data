"""League arithmetic for custom windows, with hand-computed values."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from mind_the_data.publish.league import (
    CLOSURE_SHARE_PROVISIONAL,
    MIN_OPEN_SHARE_TO_RANK,
    PROVISIONAL_COMPLETENESS,
    Standing,
    Window,
    league_from_daily,
    league_from_weekly,
    line_of_the_week,
    measured_view,
    measured_withheld_reason,
    monday_mornings,
    provisional_reasons,
    rank_standings,
    withhold_stale_measured,
    wooden_spoon,
    worst_incident,
)
from mind_the_data.publish.marts import (
    BuildTimes,
    DailyRow,
    IncidentRow,
    LineInfo,
    MeasuredDailyRow,
    MeasuredSums,
    MondayMorningRow,
    RecordedMarts,
    WeeklyRow,
    parse_build_times,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "marts"


def line(line_id: str, name: str | None = None) -> LineInfo:
    return LineInfo(line_id, name or line_id.title(), "tube", "#000000", False)


def standing(
    line_id: str,
    *,
    scored: int = 600,
    good: int = 600,
    weighted: float = 0.0,
    scheduled: int = 600,
    observed: int | None = None,
    works: int = 0,
    time_weighted: float | None = None,
) -> Standing:
    # Every scored minute at weight 1 unless the test says otherwise.
    return Standing(
        line=line(line_id),
        scheduled_minutes=scheduled,
        observed_minutes=scheduled if observed is None else observed,
        engineering_works_minutes=works,
        scored_minutes=scored,
        good_service_minutes=good,
        weighted_disruption_minutes=weighted,
        time_weighted_scored_minutes=float(scored) if time_weighted is None else time_weighted,
        incidents=0,
        median_recovery_minutes=None,
    )


def daily(line_id: str, day: date, **minutes: float) -> DailyRow:
    base = {
        "scheduled_minutes": 0,
        "observed_minutes": 0,
        "missing_minutes": 0,
        "engineering_works_minutes": 0,
        "service_closed_minutes": 0,
        "scored_minutes": 0,
        "good_service_minutes": 0,
        "disrupted_minutes": 0,
        "weighted_disruption_minutes": 0.0,
        "time_weighted_scored_minutes": 0.0,
        "incidents": 0,
    }
    base.update(minutes)
    return DailyRow(line_id=line_id, london_date=day, **base)  # type: ignore[arg-type]


def incident(
    line_id: str,
    day: date,
    duration: int,
    *,
    recovery: int | None = None,
    weight: float = 1.0,
    hour: int = 8,
    severity_weighted: float | None = None,
) -> IncidentRow:
    return IncidentRow(
        line_id=line_id,
        started_at_utc=datetime(day.year, day.month, day.day, hour, 0),
        started_at_london=datetime(day.year, day.month, day.day, hour, 0),
        start_london_date=day,
        duration_minutes=duration,
        is_open=recovery is None,
        recovery_minutes=recovery,
        peak_severity_weight=weight,
        severity_weighted_minutes=duration * weight
        if severity_weighted is None
        else severity_weighted,
        worst_status_description="Minor Delays",
        first_reason=None,
    )


def test_index_and_percentages_from_minutes() -> None:
    # D19: 600 scored minutes, 300 of them peak (weight 2) and 300 off-peak
    # (weight 1), so 900 time-weighted minutes. 30 Severe Delays peak minutes
    # (3 x 2 = 6 each) give 180 weighted minutes: index 100 x 180 / 900 = 20.
    s = standing(
        "a", scored=600, good=570, weighted=180.0, scheduled=800, observed=760, time_weighted=900
    )
    assert s.disruption_index == pytest.approx(20.0)
    assert s.good_service_pct == pytest.approx(95.0)
    assert s.completeness == pytest.approx(0.95)
    # 100 is Suspended (8) in every minute: the top of the scale is 800.
    suspended = standing("b", scored=60, good=0, weighted=8.0 * 120, time_weighted=120)
    assert suspended.disruption_index == pytest.approx(800.0)


def test_no_scored_minutes_gives_no_index() -> None:
    s = standing("a", scored=0, good=0)
    assert s.disruption_index is None
    assert s.good_service_pct is None


def test_rank_ties_split_by_good_service_then_shared() -> None:
    # Same index for b, c, d; c has the higher Good Service %, b and d tie fully.
    ranked = rank_standings(
        [
            standing("a", weighted=60.0, good=500),  # index 100 x 60 / 600 = 10
            standing("b", weighted=6.0, good=580),  # index 1
            standing("c", weighted=6.0, good=590),  # index 1, better Good Service
            standing("d", weighted=6.0, good=580),  # index 1
            standing("e", scored=0, good=0),  # unranked
            standing("f", weighted=0.0, good=600),  # index 0
        ]
    )
    assert [(s.line.line_id, s.rank) for s in ranked] == [
        ("f", 1),
        ("c", 2),
        ("b", 3),
        ("d", 3),
        ("a", 5),
        ("e", None),
    ]


def test_provisional_reasons() -> None:
    assert provisional_reasons(standing("a")) == ()

    low = provisional_reasons(standing("a", scheduled=1000, observed=899))
    assert [r.code for r in low] == ["low_completeness"]
    assert PROVISIONAL_COMPLETENESS == 0.9

    # Exactly at the threshold counts; just under does not.
    works_at = int(1000 * CLOSURE_SHARE_PROVISIONAL)
    closed = provisional_reasons(standing("a", scheduled=1000, works=works_at))
    assert [(r.code, r.decisions) for r in closed] == [("mostly_engineering_works", ("D35",))]
    assert provisional_reasons(standing("a", scheduled=1000, works=works_at - 1)) == ()

    nothing = provisional_reasons(standing("a", scheduled=0, scored=0, good=0))
    assert [r.code for r in nothing] == ["no_scheduled_minutes"]


def test_league_from_daily_sums_minutes_not_ratios() -> None:
    d1, d2, outside = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    rows = [
        # Day 1: 100 scored (time-weighted 150), 50 weighted -> 33.3 alone.
        # Day 2: 500 scored (time-weighted 450), 10 weighted.
        daily("a", d1, scheduled_minutes=100, observed_minutes=100, scored_minutes=100,
              good_service_minutes=60, weighted_disruption_minutes=50.0,
              time_weighted_scored_minutes=150.0, incidents=1),
        daily("a", d2, scheduled_minutes=500, observed_minutes=480, scored_minutes=500,
              good_service_minutes=480, weighted_disruption_minutes=10.0,
              time_weighted_scored_minutes=450.0, incidents=2),
        daily("a", outside, scheduled_minutes=500, observed_minutes=500, scored_minutes=500,
              weighted_disruption_minutes=999.0, time_weighted_scored_minutes=500.0),
        daily("b", d1, scheduled_minutes=600, observed_minutes=600, scored_minutes=600,
              good_service_minutes=600, time_weighted_scored_minutes=600.0),
    ]  # fmt: skip
    incidents = [
        incident("a", d1, 30, recovery=30),
        incident("a", d2, 10, recovery=10),
        incident("a", d2, 50, recovery=50),
        incident("a", d2, 400),  # open: no recovery yet
        incident("a", outside, 900, recovery=900),
    ]
    league = league_from_daily(Window(d1, d2), [line("a"), line("b"), line("c")], rows, incidents)
    by_id = {s.line.line_id: s for s in league.standings}
    a = by_id["a"]
    # 60 weighted minutes over 150 + 450 = 600 time-weighted minutes: 100 x 0.1 = 10.
    assert a.disruption_index == pytest.approx(10.0)
    assert a.good_service_pct == pytest.approx(90.0)
    assert a.completeness == pytest.approx(580 / 600)
    assert a.incidents == 3
    assert a.median_recovery_minutes == 30.0
    assert [s.line.line_id for s in league.standings] == ["b", "a", "c"]
    assert by_id["c"].rank is None
    assert [r.code for r in by_id["c"].provisional_reasons] == ["no_scheduled_minutes"]
    assert a.rank_movement is None
    # The 900-minute incident starts outside the window; the open 400-minute
    # one has the most severity-weighted minutes inside it.
    assert league.worst_incident is not None
    assert league.worst_incident.incident.duration_minutes == 400
    assert league.line_of_the_week.line is None
    assert league.line_of_the_week.null_reason == "custom_window"
    assert league.completeness == pytest.approx(1180 / 1200)


def test_worst_incident_is_the_most_severity_weighted() -> None:
    day = date(2026, 11, 4)
    lines = [line("a"), line("b")]
    # 100 minutes of Minor Delays (1 each) = 100; 30 minutes Suspended (8) = 240.
    longer = incident("a", day, 100)
    heavier = incident("b", day, 30, weight=8.0, hour=10)
    picked = worst_incident(lines, [longer, heavier])
    assert picked is not None and picked.incident is heavier
    # Equal weight (240): the longer wins, then the earlier.
    long_minor = incident("a", day, 240, hour=12)
    picked = worst_incident(lines, [heavier, long_minor])
    assert picked is not None and picked.incident is long_minor
    earlier = replace(long_minor, line_id="b", started_at_utc=datetime(2026, 11, 4, 6))
    picked = worst_incident(lines, [long_minor, earlier])
    assert picked is not None and picked.incident is earlier
    assert worst_incident(lines, []) is None
    assert worst_incident([line("z")], [longer]) is None


def test_lines_mostly_under_works_are_not_ranked() -> None:
    """D22: ranked only if open for at least half the scheduled minutes."""
    assert MIN_OPEN_SHARE_TO_RANK == 0.5
    ranked = rank_standings(
        [
            standing("a", weighted=60.0),  # index 10
            # 1000 scheduled, 600 closed: open 40%, index 0, not ranked.
            standing("b", scheduled=1000, works=600, scored=400, good=400),
            # Exactly half closed: still ranked; index 100 x 50 / 500 = 10.
            standing("c", scheduled=1000, works=500, scored=500, good=450, weighted=50.0),
        ]
    )
    by_id = {s.line.line_id: s for s in ranked}
    assert by_id["b"].rank is None and by_id["b"].unranked_reason == "engineering_works"
    assert by_id["b"].disruption_index == 0.0
    # a and c share index 10; a has the higher Good Service %.
    assert (by_id["a"].rank, by_id["c"].rank) == (1, 2)
    assert by_id["c"].unranked_reason is None
    # D35 still marks c (50% closed, over 25%) provisional; its rank stands.
    assert [r.code for r in by_id["c"].provisional_reasons] == []
    assert [r.code for r in provisional_reasons(by_id["c"])] == ["mostly_engineering_works"]
    assert [s.line.line_id for s in ranked] == ["a", "c", "b"]
    # Closed all week: no index, and the reason is the works.
    closed = standing("d", scheduled=1000, works=1000, scored=0, good=0)
    assert closed.disruption_index is None
    assert closed.unranked_reason == "engineering_works"


def test_wooden_spoon_is_the_bottom_rank() -> None:
    ranked = rank_standings(
        [
            standing("a", weighted=60.0, good=500),  # index 10, rank 3
            standing("c", weighted=60.0, good=500),  # index 10, shares rank 3
            standing("b", weighted=6.0, good=580),  # index 1, rank 2
            standing("d"),  # index 0, rank 1
            standing("e", scored=0, good=0),  # unranked
            # Closed 90%: index 100 x 400 / 100 = 400, but not ranked.
            standing("f", scheduled=1000, works=900, scored=100, good=0, weighted=400.0),
        ]
    )
    spoon = wooden_spoon(ranked)
    assert spoon is not None
    assert (spoon.standing.line.line_id, spoon.standing.rank, spoon.shared_by) == ("a", 3, 2)
    assert wooden_spoon([standing("x", scored=0, good=0)]) is None


def weekly_row(
    line_id: str,
    week_start: date,
    index: float | None,
    rank: int | None,
    *,
    observed: int = 100,
    complete: bool = True,
    provisional: bool = False,
) -> WeeklyRow:
    return WeeklyRow(
        line_id=line_id,
        week_start=week_start,
        scheduled_minutes=100,
        observed_minutes=observed,
        engineering_works_minutes=0,
        scored_minutes=observed,
        good_service_minutes=observed,
        weighted_disruption_minutes=0.0,
        time_weighted_scored_minutes=float(observed),
        incidents=0,
        median_recovery_minutes=None,
        completeness=observed / 100,
        disruption_index=index,
        good_service_pct=None,
        incidents_per_100_hours=None,
        is_week_complete=complete,
        is_provisional=provisional,
        unranked_reason=None if rank is not None else "engineering_works",
        disruption_rank=rank,
        rank_movement=None,
    )


def test_line_of_the_week_by_hand() -> None:
    """The same weeks as the dbt unit test rpt_weekly_highlights_by_hand."""
    w44, w45, w46, w47, w48 = (date(2026, 10, 26) + timedelta(days=7 * i) for i in range(5))
    lines = [line(x) for x in ("victoria", "central", "jubilee", "district")]
    rows = [
        weekly_row("victoria", w44, None, None, observed=0, provisional=True),
        weekly_row("victoria", w45, 50, 1, observed=70, provisional=True),
        weekly_row("central", w45, 120, 2, observed=70, provisional=True),
        weekly_row("victoria", w46, 40, 1),
        weekly_row("central", w46, 60, 2),
        weekly_row("jubilee", w46, 60, 2),
        weekly_row("district", w46, 500, None),
        weekly_row("victoria", w47, 70, 4),
        weekly_row("central", w47, 20, 2),
        weekly_row("jubilee", w47, 40, 3),
        weekly_row("district", w47, 10, 1),
        *(
            weekly_row(r.line_id, w48, r.disruption_index, r.disruption_rank, complete=False)
            for r in (
                weekly_row("victoria", w47, 70, 4),
                weekly_row("central", w47, 20, 2),
                weekly_row("jubilee", w47, 40, 3),
                weekly_row("district", w47, 10, 1),
            )
        ),
    ]
    assert line_of_the_week(w44, lines, rows).null_reason == "no_previous_week"
    assert line_of_the_week(w45, lines, rows).null_reason == "no_previous_week"
    assert line_of_the_week(w46, lines, rows).null_reason == "previous_week_incomplete"
    best = line_of_the_week(w47, lines, rows)
    assert best.line is not None and best.line.line_id == "central"
    assert (best.previous_disruption_index, best.disruption_index, best.improvement) == (
        60,
        20,
        40,
    )
    assert best.null_reason is None
    assert line_of_the_week(w48, lines, rows).null_reason == "no_line_improved"


def test_monday_mornings_pick_the_mondays_in_the_window() -> None:
    def row(line_id: str, monday: date, rank: int | None) -> MondayMorningRow:
        return MondayMorningRow(
            line_id=line_id,
            week_start=monday,
            scheduled_minutes=180,
            scored_minutes=180,
            completeness=1.0,
            engineering_works_share=0.0,
            monday_morning_index=None if rank is None else 10.0 * rank,
            good_service_pct=None,
            is_provisional=False,
            unranked_reason=None if rank is not None else "no_scored_minutes",
            monday_morning_rank=rank,
        )

    rows = [
        row("a", date(2026, 11, 9), 2),
        row("b", date(2026, 11, 9), 1),
        row("c", date(2026, 11, 9), None),
        row("a", date(2026, 11, 2), 1),  # before the window
    ]
    # A Monday in the window that nothing observed is left out.
    rows.append(replace(row("a", date(2026, 11, 16), None), completeness=0.0))
    picked = monday_mornings(
        Window(date(2026, 11, 4), date(2026, 11, 17)), [line("a"), line("b"), line("c")], rows
    )
    assert [(m.line.line_id, m.row.monday_morning_rank) for m in picked] == [
        ("b", 1),
        ("a", 2),
        ("c", None),
    ]


def test_window() -> None:
    week = Window.for_iso_week("2026-W45")
    assert (week.start, week.end) == (date(2026, 11, 2), date(2026, 11, 8))
    assert week.iso_week == "2026-W45"
    assert Window(date(2026, 11, 4), date(2026, 11, 10)).iso_week is None
    assert Window(date(2026, 11, 4), date(2026, 11, 10)).days == 7
    assert Window.for_iso_week("2027-W01").start == date(2027, 1, 4)
    with pytest.raises(ValueError, match="before it starts"):
        Window(date(2026, 11, 5), date(2026, 11, 4))


def test_daily_sum_matches_dbt_weekly_on_a_recorded_week() -> None:
    """The custom-window path reproduces fct_line_weekly for the same seven days."""
    marts = RecordedMarts(FIXTURES)
    lines = marts.lines()
    window = Window.for_iso_week("2026-W45")
    weekly = [w for w in marts.weekly() if w.week_start == window.start]
    assert len(weekly) == len(lines) == 20

    from_daily = league_from_daily(
        window, lines, marts.daily(window.start, window.end), marts.incidents(*_w(window))
    )
    from_weekly = league_from_weekly(window.start, lines, weekly, marts.incidents(*_w(window)))
    by_id = {s.line.line_id: s for s in from_daily.standings}
    for row in weekly:
        mine = by_id[row.line_id]
        assert mine.rank == row.disruption_rank, row.line_id
        assert mine.completeness == pytest.approx(row.completeness)
        assert mine.incidents == row.incidents
        assert mine.median_recovery_minutes == pytest.approx(row.median_recovery_minutes)
        for got, want in (
            (mine.disruption_index, row.disruption_index),
            (mine.good_service_pct, row.good_service_pct),
            (mine.incidents_per_100_hours, row.incidents_per_100_hours),
        ):
            assert (got is None and want is None) or got == pytest.approx(want)
    assert [s.line.line_id for s in from_daily.standings] == [
        s.line.line_id for s in from_weekly.standings
    ]


def _w(window: Window) -> tuple[date, date]:
    return window.start, window.end


def test_wednesday_to_tuesday_window_across_two_iso_weeks() -> None:
    """The first post's shape: Wed 4 to Tue 10 Nov spans W45 and W46."""
    window = Window(date(2026, 11, 4), date(2026, 11, 10))
    days = [date(2026, 11, d) for d in range(3, 12)]  # Tue 3 to Wed 11
    rows = [
        daily("a", d, scheduled_minutes=100, observed_minutes=100, scored_minutes=100,
              good_service_minutes=90, weighted_disruption_minutes=2.0,
              time_weighted_scored_minutes=100.0, incidents=1)
        for d in days
    ]  # fmt: skip
    incidents = [
        # Starts the day before the window: belongs to 3 Nov (D18), left out.
        incident("a", date(2026, 11, 3), 2000, recovery=2000, hour=23),
        # Starts on the last day and runs past midnight: in, by its start date.
        incident("a", date(2026, 11, 10), 90, recovery=90, hour=23),
        incident("a", date(2026, 11, 4), 10, recovery=10),
        incident("a", date(2026, 11, 7), 40, recovery=40),
        incident("a", date(2026, 11, 9), 20, recovery=20),
        # Starts the day after: out.
        incident("a", date(2026, 11, 11), 5000, recovery=5000),
    ]
    league = league_from_daily(window, [line("a")], rows, incidents, datetime(2026, 11, 11, 0, 3))
    (a,) = league.standings
    assert a.scheduled_minutes == 700  # seven days, not nine
    # 14 weighted minutes over 700 time-weighted minutes: 100 x 0.02 = 2.
    assert a.disruption_index == pytest.approx(2.0)
    assert a.incidents == 7  # from fct_line_daily's own counts for the seven days
    # Even count (10, 20, 40, 90): the mean of the middle two, as dbt does.
    assert a.median_recovery_minutes == 30.0
    assert league.worst_incident is not None
    assert league.worst_incident.incident.duration_minutes == 90
    assert league.is_complete


def test_window_is_complete_only_once_data_passes_its_end() -> None:
    window = Window(date(2026, 11, 4), date(2026, 11, 10))
    assert not league_from_daily(window, [], [], [], None).is_complete
    assert not league_from_daily(window, [], [], [], datetime(2026, 11, 10, 23, 59)).is_complete
    assert league_from_daily(window, [], [], [], datetime(2026, 11, 11, 0, 0)).is_complete


def test_weekly_flags_come_from_the_mart() -> None:
    marts = RecordedMarts(FIXTURES)
    window = Window.for_iso_week("2026-W45")
    weekly = [replace(w, is_provisional=False) for w in marts.weekly()]
    league = league_from_weekly(window.start, marts.lines(), weekly, [])
    # Completeness is under 90% for every line, but dbt said not provisional.
    assert not any(
        r.code == "low_completeness" for s in league.standings for r in s.provisional_reasons
    )


# ---- Measured view (D42, D51 to D53) --------------------------------------------


def measured_line(line_id: str, *, is_measured: bool = True) -> LineInfo:
    return LineInfo(line_id, line_id.title(), "tube", "#000000", False, is_measured)


def sums(**values: float) -> MeasuredSums:
    base: dict[str, float] = {
        "scheduled_measurement_hours": 10,
        "observed_measurement_hours": 10,
        "observed_trains": 80,
        "observed_hours_scheduled_trains": 100,
        "measured_hours": 8,
        "excess_wait_hours": 8,
        "sum_excess_wait_seconds": 240.0,
        "long_gaps": 2,
        "fallback_measured_hours": 0,
        "gate_judged_hours": 10,
        "gate_in_band_hours": 8,
        "good_service_measured_hours": 4,
        "honesty_gap_hours": 1,
    }
    base.update(values)
    return MeasuredSums(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line_info", "values", "reason"),
    [
        (measured_line("dlr", is_measured=False), {}, "line_not_measured"),
        (measured_line("circle"), {}, "shared_section_misfiled"),
        (measured_line("hammersmith-city"), {}, "shared_section_misfiled"),
        (measured_line("a"), {"measured_hours": 0}, "no_measured_hours"),
        (measured_line("a"), {"gate_judged_hours": 0, "gate_in_band_hours": 0}, "gate_not_judged"),
        # 6 of 9 in band is 66.7%: more than 30% out (D51).
        (measured_line("a"), {"gate_judged_hours": 9, "gate_in_band_hours": 6}, "gate_failed"),
        # 7 of 10 is exactly 30% out: passes.
        (measured_line("a"), {"gate_in_band_hours": 7}, None),
        # 4 of 10 scheduled hours measured is under half (D42).
        (measured_line("a"), {"measured_hours": 4}, "too_few_measured_hours"),
        (measured_line("a"), {"measured_hours": 5}, None),
    ],
)
def test_measured_withheld_reason_by_hand(
    line_info: LineInfo, values: dict[str, float], reason: str | None
) -> None:
    assert measured_withheld_reason(line_info, sums(**values)) == reason


def test_measured_view_numbers_by_hand() -> None:
    view = measured_view(measured_line("a"), sums(), experienced_rank=3)
    # 240 s over 8 hours = 30 s = 0.5 min; 2 long gaps per 8 hours = 25 per 100.
    assert view.excess_wait_minutes == pytest.approx(0.5)
    assert view.long_gaps_per_100_hours == pytest.approx(25)
    assert (view.honesty_gap_hours, view.honesty_gap_share) == (1.0, 0.25)
    assert view.trains_run_share == pytest.approx(0.8)
    assert (view.measured_share, view.gate_pass_share) == (0.8, 0.8)
    assert view.experienced_rank == 3 and not view.is_weak_evidence
    # Circle and H&C trains run stays withheld if their view is ever shown (ADR-014).
    circle = measured_view(measured_line("circle"), sums(), None)
    assert circle.trains_run_share is None
    assert circle.trains_run_withheld_reason == "shared_section_misfiled"
    weak = measured_view(measured_line("a"), sums(fallback_measured_hours=1), None)
    assert weak.is_weak_evidence


def test_custom_window_measured_view_and_rank() -> None:
    """Sums over the window's days, then fct_line_weekly's rules (D51 to D53)."""
    window = Window(date(2026, 11, 4), date(2026, 11, 5))
    lines = [
        measured_line("a"),
        measured_line("b"),
        measured_line("c"),
        measured_line("weak"),
        measured_line("gone", is_measured=False),
    ]
    day1, day2, outside = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    rows = [
        # a: (240 + 0) s over 8 + 8 hours = 0.25 min.
        MeasuredDailyRow("a", day1, sums()),
        MeasuredDailyRow("a", day2, sums(sum_excess_wait_seconds=0.0)),
        MeasuredDailyRow("a", outside, sums(sum_excess_wait_seconds=99_999.0)),
        # b: 0.5 min, ranks below a.
        MeasuredDailyRow("b", day1, sums()),
        # c: (2 + 10) of 20 judged hours in band = 60%, more than 30% out.
        MeasuredDailyRow("c", day1, sums(gate_in_band_hours=2)),
        MeasuredDailyRow("c", day2, sums(gate_in_band_hours=10)),
        # weak: the best excess wait, but against an estimated timetable.
        MeasuredDailyRow(
            "weak", day1, sums(sum_excess_wait_seconds=-480.0, fallback_measured_hours=8)
        ),
    ]
    daily_rows = [
        daily(
            x.line_id,
            day1,
            scheduled_minutes=60,
            observed_minutes=60,
            scored_minutes=60,
            good_service_minutes=60,
            time_weighted_scored_minutes=60.0,
        )
        for x in lines
    ]
    league = league_from_daily(window, lines, daily_rows, [], measured_daily=rows)
    by_id = {s.line.line_id: s for s in league.standings}
    a, b = by_id["a"].measured, by_id["b"].measured
    assert a is not None and b is not None
    assert a.excess_wait_minutes == pytest.approx(0.25) and a.experienced_rank == 1
    assert b.excess_wait_minutes == pytest.approx(0.5) and b.experienced_rank == 2
    assert by_id["c"].measured is None and by_id["c"].measured_withheld_reason == "gate_failed"
    weak = by_id["weak"].measured
    assert weak is not None and weak.is_weak_evidence and weak.experienced_rank is None
    assert by_id["gone"].measured_withheld_reason == "line_not_measured"


@pytest.mark.parametrize("iso_week", ["2026-W45", "2026-W46"])
def test_measured_daily_sum_matches_dbt_weekly(iso_week: str) -> None:
    """Python's window sums and rules reproduce fct_line_weekly's measured view.

    The measured recordings come from the dev build (fct_line_measured_daily
    and fct_line_weekly's measured columns).
    """
    marts = RecordedMarts(FIXTURES)
    lines = marts.lines()
    window = Window.for_iso_week(iso_week)
    weekly = [w for w in marts.weekly() if w.week_start == window.start]
    from_daily = league_from_daily(
        window,
        lines,
        marts.daily(window.start, window.end),
        [],
        measured_daily=marts.measured_daily(window.start, window.end),
    )
    by_id = {s.line.line_id: s for s in from_daily.standings}
    published = 0
    for row in weekly:
        mine = by_id[row.line_id]
        assert mine.measured_withheld_reason == row.measured_withheld_reason, row.line_id
        if row.measured_withheld_reason is None:
            published += 1
            assert mine.measured is not None and row.measured_sums is not None
            assert mine.measured.experienced_rank == row.experienced_rank, row.line_id
            want = measured_view(mine.line, row.measured_sums, row.experienced_rank)
            # Summing seconds day by day can differ in the last bit.
            assert mine.measured.excess_wait_minutes == pytest.approx(want.excess_wait_minutes)
            assert replace(mine.measured, excess_wait_minutes=None) == replace(
                want, excess_wait_minutes=None
            )
    assert published >= 8


# A gate failure on Wednesday 18 November skips the measured tables; they
# were last built at 03:30 London on Tuesday 17 November (UTC in winter).
STALE_BUILD = BuildTimes(
    declared_built_at=datetime(2026, 11, 18, 3, 35),
    measured_built_at=datetime(2026, 11, 17, 3, 40),
    measured_built_london_date=date(2026, 11, 17),
)


def test_build_times_measured_is_final_for() -> None:
    # Built in the same run (after the declared table): every window is final.
    same_run = replace(STALE_BUILD, measured_built_at=datetime(2026, 11, 18, 3, 40))
    assert same_run.measured_is_final_for(date(2026, 11, 22))
    # Stale: W46 ended on Sunday 15 November, before the last measured build.
    assert STALE_BUILD.measured_is_final_for(date(2026, 11, 15))
    assert STALE_BUILD.measured_is_final_for(date(2026, 11, 16))
    # Tuesday 17 November itself was only partly in the tables.
    assert not STALE_BUILD.measured_is_final_for(date(2026, 11, 17))
    assert not STALE_BUILD.measured_is_final_for(date(2026, 11, 22))
    # Never built: nothing is final.
    never = BuildTimes(datetime(2026, 11, 18, 3, 35), None, None)
    assert not never.measured_is_final_for(date(2026, 11, 1))


def test_stale_measured_weeks_are_withheld_not_partial() -> None:
    """A gate failure keeps the declared rows and withholds unfinished measured weeks."""
    lines = [measured_line("a"), measured_line("dlr", is_measured=False)]
    w46, w47 = date(2026, 11, 9), date(2026, 11, 16)
    rows = [
        replace(weekly_row(x, week, 10.0, 1), measured_sums=sums(), experienced_rank=1)
        for x in ("a",)
        for week in (w46, w47)
    ] + [
        replace(weekly_row("dlr", w47, 20.0, 2), measured_withheld_reason="line_not_measured"),
    ]
    out = {(r.line_id, r.week_start): r for r in withhold_stale_measured(rows, lines, STALE_BUILD)}
    # W46 had ended when the measured tables were built: kept.
    assert out[("a", w46)].measured_sums == sums() and out[("a", w46)].experienced_rank == 1
    # W47 was in progress: withheld, never shown partial.
    stale = out[("a", w47)]
    assert stale.measured_sums is None and stale.experienced_rank is None
    assert stale.measured_withheld_reason == "measured_not_rebuilt"
    # The declared view is untouched, and unmeasured lines keep their reason.
    assert (stale.disruption_index, stale.disruption_rank) == (10.0, 1)
    assert out[("dlr", w47)].measured_withheld_reason == "line_not_measured"
    # No build times (an old recording): rows pass through.
    assert withhold_stale_measured(rows, lines, None) == rows


def test_custom_window_withholds_measured_when_tables_are_stale() -> None:
    window = Window(date(2026, 11, 16), date(2026, 11, 17))
    lines = [measured_line("a"), measured_line("dlr", is_measured=False)]
    daily_rows = [
        daily(
            x.line_id,
            window.start,
            scheduled_minutes=60,
            observed_minutes=60,
            scored_minutes=60,
            good_service_minutes=60,
            time_weighted_scored_minutes=60.0,
        )
        for x in lines
    ]
    measured = [MeasuredDailyRow("a", window.start, sums())]
    league = league_from_daily(
        window, lines, daily_rows, [], measured_daily=measured, build_times=STALE_BUILD
    )
    by_id = {s.line.line_id: s for s in league.standings}
    assert by_id["a"].measured is None
    assert by_id["a"].measured_withheld_reason == "measured_not_rebuilt"
    assert by_id["a"].rank == 1 and by_id["dlr"].rank == 1
    assert by_id["dlr"].measured_withheld_reason == "line_not_measured"


def test_build_times_parse_athena_timestamps() -> None:
    times = parse_build_times(
        {
            "declared_built_at": "2026-11-18 03:35:02.123456",
            "measured_built_at": "2026-11-17 03:40:11.000000",
            "measured_built_london_date": "2026-11-17",
        }
    )
    assert times == BuildTimes(
        datetime(2026, 11, 18, 3, 35, 2, 123456),
        datetime(2026, 11, 17, 3, 40, 11),
        date(2026, 11, 17),
    )
    # The measured table was never built.
    never = parse_build_times(
        {
            "declared_built_at": "2026-11-18 03:35:02.000000",
            "measured_built_at": None,
            "measured_built_london_date": None,
        }
    )
    assert never is not None and not never.measured_is_final_for(date(2026, 11, 1))
    assert parse_build_times({"declared_built_at": None}) is None
