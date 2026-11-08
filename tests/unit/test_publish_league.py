"""League arithmetic for custom windows, with hand-computed values."""

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from mind_the_data.publish.league import (
    CLOSURE_SHARE_PROVISIONAL,
    PROVISIONAL_COMPLETENESS,
    Standing,
    Window,
    league_from_daily,
    league_from_weekly,
    provisional_reasons,
    rank_standings,
    worst_incident,
)
from mind_the_data.publish.marts import DailyRow, IncidentRow, LineInfo, RecordedMarts

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
) -> Standing:
    return Standing(
        line=line(line_id),
        scheduled_minutes=scheduled,
        observed_minutes=scheduled if observed is None else observed,
        engineering_works_minutes=works,
        scored_minutes=scored,
        good_service_minutes=good,
        weighted_disruption_minutes=weighted,
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
        worst_status_description="Minor Delays",
        first_reason=None,
    )


def test_index_and_percentages_from_minutes() -> None:
    # 30 weighted minutes over 600 scored minutes (10 hours) = 300 per 100 hours.
    s = standing("a", scored=600, good=450, weighted=30.0, scheduled=800, observed=760)
    assert s.disruption_index == pytest.approx(300.0)
    assert s.good_service_pct == pytest.approx(75.0)
    assert s.completeness == pytest.approx(0.95)


def test_no_scored_minutes_gives_no_index() -> None:
    s = standing("a", scored=0, good=0)
    assert s.disruption_index is None
    assert s.good_service_pct is None


def test_rank_ties_split_by_good_service_then_shared() -> None:
    # Same index for b, c, d; c has the higher Good Service %, b and d tie fully.
    ranked = rank_standings(
        [
            standing("a", weighted=60.0, good=500),  # index 600
            standing("b", weighted=6.0, good=580),  # index 60
            standing("c", weighted=6.0, good=590),  # index 60, better Good Service
            standing("d", weighted=6.0, good=580),  # index 60
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
    assert [(r.code, r.decisions) for r in closed] == [("mostly_engineering_works", ("D22", "D25"))]
    assert provisional_reasons(standing("a", scheduled=1000, works=works_at - 1)) == ()

    nothing = provisional_reasons(standing("a", scheduled=0, scored=0, good=0))
    assert [r.code for r in nothing] == ["no_scheduled_minutes"]


def test_league_from_daily_sums_minutes_not_ratios() -> None:
    d1, d2, outside = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    rows = [
        # Day 1: 100 scored, 50 weighted -> 3000 alone. Day 2: 500 scored, 10 weighted.
        daily("a", d1, scheduled_minutes=100, observed_minutes=100, scored_minutes=100,
              good_service_minutes=60, weighted_disruption_minutes=50.0, incidents=1),
        daily("a", d2, scheduled_minutes=500, observed_minutes=480, scored_minutes=500,
              good_service_minutes=480, weighted_disruption_minutes=10.0, incidents=2),
        daily("a", outside, scheduled_minutes=500, observed_minutes=500, scored_minutes=500,
              weighted_disruption_minutes=999.0),
        daily("b", d1, scheduled_minutes=600, observed_minutes=600, scored_minutes=600,
              good_service_minutes=600),
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
    # 60 weighted minutes over 600 scored minutes = 600 per 100 hours.
    assert a.disruption_index == pytest.approx(600.0)
    assert a.good_service_pct == pytest.approx(90.0)
    assert a.completeness == pytest.approx(580 / 600)
    assert a.incidents == 3
    assert a.median_recovery_minutes == 30.0
    assert [s.line.line_id for s in league.standings] == ["b", "a", "c"]
    assert by_id["c"].rank is None
    assert [r.code for r in by_id["c"].provisional_reasons] == ["no_scheduled_minutes"]
    assert a.rank_movement is None
    # The 900-minute incident starts outside the window.
    assert league.worst_incident is not None
    assert league.worst_incident.incident.duration_minutes == 400
    assert league.completeness == pytest.approx(1180 / 1200)


def test_worst_incident_ties() -> None:
    day = date(2026, 11, 4)
    lines = [line("a"), line("b")]
    longer = incident("a", day, 100)
    heavier = incident("b", day, 100, weight=5.0, hour=10)
    picked = worst_incident(lines, [longer, heavier])
    assert picked is not None and picked.incident is heavier
    earlier = replace(heavier, line_id="a", started_at_utc=datetime(2026, 11, 4, 6))
    picked = worst_incident(lines, [heavier, earlier])
    assert picked is not None and picked.incident is earlier
    assert worst_incident(lines, []) is None
    assert worst_incident([line("z")], [longer]) is None


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
              good_service_minutes=90, weighted_disruption_minutes=2.0, incidents=1)
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
    # 14 weighted minutes over 700 scored minutes = 120 per 100 hours.
    assert a.disruption_index == pytest.approx(120.0)
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
