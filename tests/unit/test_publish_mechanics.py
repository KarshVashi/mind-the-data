"""M6 league mechanics: recovery, strikes, divisions, seasons and sensitivity."""

import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import ValidationError

from mind_the_data.publish import export
from mind_the_data.publish.documents import (
    Stamp,
    dbt_vars,
    divisions_document,
    season_document,
)
from mind_the_data.publish.league import PROVISIONAL_COMPLETENESS, league_from_weekly
from mind_the_data.publish.marts import (
    DivisionMonthRow,
    IncidentRow,
    LineInfo,
    RecordedMarts,
    SeasonRow,
    SeedingRow,
    StatusMixRow,
    StrikesExcludedRow,
    WeeklyRow,
    parse_strikes_excluded,
)
from mind_the_data.publish.mechanics import (
    EXCLUDING_STATUSES,
    complete_weeks,
    division_state,
    load_industrial_action,
    months_between,
    recovery_table,
    strikes_excluded_for_week,
)
from mind_the_data.publish.schemas import validate
from mind_the_data.publish.sensitivity import (
    RankedLine,
    WeightSet,
    average_ranks,
    competition_ranks,
    indexes_under,
    load_weight_sets,
    sensitivity_document,
    spearman,
    week_sensitivity,
)

ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = ROOT / "transform"
FIXTURES = ROOT / "tests" / "fixtures" / "marts"
STAMP = Stamp(generated_at=datetime(2026, 11, 14, 20, 0, tzinfo=UTC), git_sha="0123abc")
WEEK = date(2026, 11, 2)


def line(line_id: str, mode: str = "tube") -> LineInfo:
    return LineInfo(line_id, line_id.title(), mode, "#000000", False)


@pytest.fixture
def marts() -> RecordedMarts:
    return RecordedMarts(FIXTURES)


# ---- Recovery Time (D59, provisional on D18) ------------------------------


def incident(line_id: str, recovery: int | None) -> IncidentRow:
    start = datetime(2026, 11, 10, 8, 0)
    return IncidentRow(
        line_id=line_id,
        started_at_utc=start,
        started_at_london=start,
        start_london_date=start.date(),
        duration_minutes=recovery or 5,
        is_open=recovery is None,
        recovery_minutes=recovery,
        peak_severity_weight=3.0,
        severity_weighted_minutes=3.0 * (recovery or 5),
        worst_status_description="Severe Delays",
        first_reason=None,
    )


def test_recovery_table_by_hand() -> None:
    """The case of rpt_recovery_time_by_hand: Central 1, Jubilee 2, Victoria 3."""
    lines = [line("victoria"), line("central"), line("jubilee"), line("district")]
    incidents = [
        incident("victoria", 10),
        incident("victoria", 30),
        incident("victoria", None),
        incident("central", 5),
        incident("central", 15),
        incident("central", 40),
        incident("jubilee", 20),
    ]
    table = recovery_table(lines, incidents)
    got = [
        (e.line.line_id, e.rank, e.median_recovery_minutes, e.mean_recovery_minutes) for e in table
    ]
    assert got == [
        ("central", 1, 15.0, 20.0),
        ("jubilee", 2, 20.0, 20.0),
        ("victoria", 3, 20.0, 20.0),
        ("district", None, None, None),
    ]
    victoria = table[2]
    assert (victoria.incidents, victoria.recovered_incidents, victoria.open_incidents) == (3, 2, 1)
    assert victoria.longest_recovery_minutes == 30
    assert table[3].unranked_reason == "no_recovered_incidents"


def test_recovery_table_shares_equal_ranks() -> None:
    table = recovery_table(
        [line("a"), line("b"), line("c")],
        [incident("a", 10), incident("b", 10), incident("c", 30)],
    )
    assert [(e.line.line_id, e.rank) for e in table] == [("a", 1), ("b", 1), ("c", 3)]


def test_recovery_medians_match_the_weekly_mart(marts: RecordedMarts) -> None:
    """The Python median is fct_line_weekly's median_recovery_minutes, line by line."""
    lines = marts.lines()
    weekly = [w for w in marts.weekly() if w.week_start == WEEK]
    incidents = marts.incidents(WEEK, WEEK + timedelta(days=6))
    table = {e.line.line_id: e for e in recovery_table(lines, incidents)}
    for row in weekly:
        assert table[row.line_id].median_recovery_minutes == row.median_recovery_minutes


# ---- Industrial action (D60) ---------------------------------------------


def test_industrial_action_seed_is_sourced_and_consistent() -> None:
    actions = load_industrial_action(TRANSFORM)
    with (TRANSFORM / "seeds" / "lines.csv").open(newline="") as f:
        lines = list(csv.DictReader(f))
    modes = {r["mode_name"] for r in lines}
    ids = {r["line_id"] for r in lines}
    assert len({a.action_id for a in actions}) == len(actions)
    for a in actions:
        assert a.source_url.startswith("https://"), a.action_id
        assert a.source_publisher and a.source_title
        assert a.start_london_date <= a.end_london_date
        assert a.source_date <= a.end_london_date
        assert (a.applies_to == "mode" and a.applies_to_id in modes) or (
            a.applies_to == "line" and a.applies_to_id in ids
        ), a.action_id
    tube_26_nov = next(a for a in actions if a.action_id == "rmt-tube-2026-11-26")
    assert tube_26_nov.excludes_days
    assert (tube_26_nov.start_london_date, tube_26_nov.end_london_date) == (
        date(2026, 11, 26),
        date(2026, 11, 27),
    )
    assert tube_26_nov.applies_to_line(line("victoria"))
    assert not tube_26_nov.applies_to_line(line("dlr", "dlr"))
    called_off = [a for a in actions if a.status == "called_off"]
    assert called_off and not any(a.excludes_days for a in called_off)
    assert EXCLUDING_STATUSES == {"held", "announced"}


def strikes_row(line_id: str, week: date, rank: int | None, strike_days: int) -> StrikesExcludedRow:
    return StrikesExcludedRow(
        line_id=line_id,
        week_start=week,
        strike_days=strike_days,
        week_has_industrial_action=strike_days > 0,
        scheduled_minutes=100,
        observed_minutes=100,
        disruption_index=10.0,
        good_service_pct=90.0,
        completeness=1.0,
        is_provisional=False,
        unranked_reason=None,
        disruption_rank=rank,
    )


def test_strikes_excluded_for_week_orders_by_rank() -> None:
    week = date(2026, 11, 23)
    variant = strikes_excluded_for_week(
        week, [strikes_row("b", week, 2, 0), strikes_row("a", week, 1, 1)]
    )
    assert variant is not None
    assert [r.line_id for r in variant.rows] == ["a", "b"]
    assert not variant.is_same_as_main
    assert strikes_excluded_for_week(date(2026, 11, 30), variant.rows) is None


def test_strikes_excluded_equals_the_main_league_without_strikes(marts: RecordedMarts) -> None:
    """PLAN 7.4: a week with no industrial action publishes the main league twice."""
    lines = marts.lines()
    league = league_from_weekly(
        WEEK,
        lines,
        marts.weekly(),
        marts.incidents(WEEK, WEEK + timedelta(days=6)),
        strikes_excluded=marts.strikes_excluded(),
    )
    variant = league.strikes_excluded
    assert variant is not None and variant.is_same_as_main
    main = {s.line.line_id: s for s in league.standings}
    assert {r.line_id for r in variant.rows} == set(main)
    for row in variant.rows:
        standing = main[row.line_id]
        assert row.disruption_rank == standing.rank
        assert row.unranked_reason == standing.unranked_reason
        assert row.strike_days == 0
        if standing.disruption_index is None:
            assert row.disruption_index is None
        else:
            assert row.disruption_index == pytest.approx(standing.disruption_index)


# ---- Divisions (ADR-015, D56, D57) ------------------------------------------


def weekly_row(week: date, observed: int, *, complete: bool = True) -> WeeklyRow:
    return WeeklyRow(
        line_id="victoria",
        week_start=week,
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
        disruption_index=0.0,
        good_service_pct=100.0,
        incidents_per_100_hours=0.0,
        is_week_complete=complete,
        is_provisional=observed < 90,
        unranked_reason=None,
        disruption_rank=1,
        rank_movement=None,
    )


def test_complete_weeks_follow_the_seeding_rule() -> None:
    rows = [
        weekly_row(date(2026, 11, 2), 70),  # began on a Wednesday: 70 % observed
        weekly_row(date(2026, 11, 9), 95),
        weekly_row(date(2026, 11, 16), 90),
        weekly_row(date(2026, 11, 23), 100, complete=False),
    ]
    assert complete_weeks(rows, PROVISIONAL_COMPLETENESS) == [date(2026, 11, 9), date(2026, 11, 16)]


def test_months_between() -> None:
    assert months_between(date(2026, 11, 2), date(2027, 1, 3)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]


def seeding_row(line_id: str, position: int, division: int) -> SeedingRow:
    return SeedingRow(
        line_id=line_id,
        seed_position=position,
        division=division,
        disruption_index=10.0 * position,
        good_service_pct=90.0,
        unranked_reason=None,
        seeding_start=date(2026, 11, 9),
        seeding_end=date(2026, 11, 22),
        seeding_weeks=2,
        complete_weeks_available=2,
        first_division_month=date(2026, 12, 1),
        seeding_status="provisional",
    )


def month_row(
    line_id: str, division: int, rank: int, movement: str, *, complete: bool
) -> DivisionMonthRow:
    nxt = {"promoted": 1, "relegated": 2}.get(movement, division)
    return DivisionMonthRow(
        line_id=line_id,
        month_start=date(2026, 12, 1),
        division=division,
        joined_by="seeded",
        scheduled_minutes=100,
        observed_minutes=95,
        disruption_index=float(rank),
        good_service_pct=90.0,
        completeness=0.95,
        is_provisional=False,
        unranked_reason=None,
        division_rank=rank,
        movement=movement,
        next_division=nxt,
        is_month_complete=complete,
    )


LINES = [line("victoria"), line("central"), line("jubilee"), line("district")]
SEEDING = [
    seeding_row("victoria", 1, 1),
    seeding_row("central", 2, 1),
    seeding_row("jubilee", 3, 2),
    seeding_row("district", 4, 2),
]


def test_not_seeded_is_explicit_and_has_no_table() -> None:
    state = division_state(date(2026, 11, 1), [], [], complete_weeks_available=0)
    doc = divisions_document(state, LINES, STAMP)
    validate(doc, "division")
    assert doc["status"] == "not_seeded"
    assert doc["seeding"] is None and doc["divisions"] == []
    assert doc["completeness"] is None
    # A not_seeded file cannot carry a table.
    with pytest.raises(ValidationError):
        validate({**doc, "divisions": [{"division": 1, "name": "x", "lines": []}]}, "division")


def test_seeded_before_the_first_division_month() -> None:
    state = division_state(date(2026, 11, 1), SEEDING, [], complete_weeks_available=2)
    doc = divisions_document(state, LINES, STAMP)
    validate(doc, "division")
    assert doc["status"] == "seeded"
    assert doc["seeding"]["status"] == "provisional"
    assert doc["seeding"]["first_division_month"] == "2026-12"
    assert [r["line_id"] for r in doc["seeding"]["lines"]] == [
        "victoria",
        "central",
        "jubilee",
        "district",
    ]
    assert doc["divisions"] == []


@pytest.mark.parametrize(("complete", "status"), [(False, "in_progress"), (True, "complete")])
def test_division_month(complete: bool, status: str) -> None:
    rows = [
        month_row("district", 2, 2, "stays", complete=complete),
        month_row("victoria", 1, 2, "relegated", complete=complete),
        month_row("central", 1, 1, "stays", complete=complete),
        month_row("jubilee", 2, 1, "promoted", complete=complete),
    ]
    state = division_state(date(2026, 12, 1), SEEDING, rows, complete_weeks_available=5)
    doc = divisions_document(state, LINES, STAMP)
    validate(doc, "division")
    assert doc["status"] == status
    assert doc["data_window"]["start"] == "2026-12-01"
    assert doc["data_window"]["end"] == "2026-12-31"
    assert doc["completeness"] == 0.95
    one, two = doc["divisions"]
    assert one["name"] == "Division One"
    assert [(r["line_id"], r["movement"]) for r in one["lines"]] == [
        ("central", "stays"),
        ("victoria", "relegated"),
    ]
    assert [(r["line_id"], r["movement"], r["next_division"]) for r in two["lines"]] == [
        ("jubilee", "promoted", 1),
        ("district", "stays", 2),
    ]


def test_export_writes_the_current_month_not_seeded(marts: RecordedMarts) -> None:
    """Today's recording: no complete week yet, so November is explicitly not seeded."""
    _league, outputs = export.build_outputs(marts, WEEK, STAMP)
    by_path = {o.path: o.document for o in outputs}
    assert by_path["divisions/2026-11.json"]["status"] == "not_seeded"
    assert by_path["divisions/2026-11.json"]["complete_weeks_available"] == 0
    index = by_path["meta/index.json"]
    assert index["months"] == [
        {"month": "2026-11", "status": "not_seeded", "path": "divisions/2026-11.json"}
    ]
    assert index["seasons"] == [{"season": "2026-27", "path": "season/2026-27.json"}]


def test_methodology_division_settings_follow_dbt(marts: RecordedMarts) -> None:
    _league, outputs = export.build_outputs(marts, WEEK, STAMP)
    methodology = next(o.document for o in outputs if o.path == "meta/methodology.json")
    variables = dbt_vars(TRANSFORM)
    assert methodology["divisions"]["seeding_weeks"] == variables["division_seeding_weeks"] == 2
    assert methodology["divisions"]["full_seeding_weeks"] == 4
    assert methodology["divisions"]["moves_per_month"] == variables["division_moves"] == 3
    assert methodology["recovery_time"]["definition_status"] == "provisional_d18"
    assert len(methodology["industrial_action"]) == len(load_industrial_action(TRANSFORM))


# ---- Season (D58) ----------------------------------------------------------


def season_row(line_id: str, rank: int | None, reason: str | None = None) -> SeasonRow:
    return SeasonRow(
        line_id=line_id,
        season="2026-27",
        season_start=date(2026, 4, 1),
        season_end=date(2027, 3, 31),
        first_observed_date=date(2026, 11, 4),
        last_observed_date=date(2026, 11, 14),
        scheduled_minutes=1000,
        observed_minutes=950,
        engineering_works_minutes=120,
        incidents=2,
        disruption_index=12.5,
        good_service_pct=97.0,
        incidents_per_100_hours=1.2,
        completeness=0.95,
        is_provisional=False,
        unranked_reason=reason,
        season_rank=rank,
        is_season_complete=False,
    )


def test_season_document_is_season_to_date() -> None:
    rows = [
        season_row("central", 2),
        season_row("district", None, "engineering_works"),
        season_row("victoria", 1),
    ]
    doc = season_document("2026-27", rows, LINES, STAMP)
    validate(doc, "season")
    assert [r["line_id"] for r in doc["lines"]] == ["victoria", "central", "district"]
    assert doc["data_window"]["start"] == "2026-11-04"
    assert doc["data_window"]["end"] == "2026-11-14"
    assert doc["is_season_complete"] is False
    assert doc["lines"][0]["engineering_works_hours"] == 2.0
    with pytest.raises(ValueError, match="no rows"):
        season_document("2027-28", rows, LINES, STAMP)


# ---- Sensitivity (PLAN 7.5, D61) -------------------------------------------


def test_spearman_by_hand() -> None:
    # d = (0, 1, 1, 0): rho = 1 - 6 x 2 / (4 x 15) = 0.8.
    assert spearman([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1], [1, 2]) is None
    assert spearman([1], [1]) is None


def test_ranks_with_ties() -> None:
    keys = [(5.0, 0.0), (1.0, 0.0), (5.0, 0.0), (9.0, 0.0)]
    assert competition_ranks(keys) == [2, 1, 2, 4]
    assert average_ranks(keys) == [2.5, 1.0, 2.5, 4.0]


def test_weight_sets_from_the_seeds() -> None:
    base, sets = load_weight_sets(TRANSFORM / "seeds")
    assert [s.name for s in sets] == ["flat_time", "linear_severity", "peak_heavy"]
    flat, linear, peak = sets
    assert set(flat.time_band.values()) == {1.0}
    assert flat.severity == base.severity
    # Bus Service: 4 when unplanned, still 0 when planned.
    assert linear.severity_weight(8, False) == 4.0
    assert linear.severity_weight(8, True) == 0.0
    assert linear.severity_weight(2, True) == 5.0
    assert linear.time_band == base.time_band
    assert peak.time_band["am_peak"] == 3.0 and peak.time_band["weekend"] == 0.5
    assert peak.time_band["inter_peak"] == base.time_band["inter_peak"]


def mix(line_id: str, code: int, band: str, minutes: int) -> StatusMixRow:
    return StatusMixRow(line_id, WEEK, code, False, band, minutes)


SEVERITY = {6: (3.0, 3.0), 9: (1.0, 1.0), 10: (0.0, 0.0)}
BANDS = {"am_peak": 2.0, "inter_peak": 1.0}
BASE = WeightSet("base", "base", SEVERITY, BANDS)
FLAT = WeightSet("flat", "flat", SEVERITY, {"am_peak": 1.0, "inter_peak": 1.0})


def test_indexes_under_a_weight_set_by_hand() -> None:
    # Line a: 10 peak minutes Minor Delays, 30 off-peak Good Service.
    # Base: 100 x (10 x 1 x 2) / (10 x 2 + 30 x 1) = 2,000 / 50 = 40.
    # Flat: 100 x 10 / 40 = 25.
    rows = [mix("a", 9, "am_peak", 10), mix("a", 10, "inter_peak", 30)]
    assert indexes_under(rows, BASE) == {"a": pytest.approx(40.0)}
    assert indexes_under(rows, FLAT) == {"a": pytest.approx(25.0)}


def test_week_sensitivity_by_hand() -> None:
    """Four lines; flat time weights swap the middle two.

    a: 10 peak Severe (3) + 10 off-peak Good: base 100 x 60 / 30 = 200, flat
    100 x 30 / 20 = 150. b: 10 off-peak Severe + 10 peak Good: base 100 x 30
    / 30 = 100, flat 150 too, so a and b tie under flat and split on Good
    Service (b 60 % > a 50 %). c: 20 off-peak Minor: 100 under both. d: 20
    off-peak Good: 0 under both.
    Base order d (0), b (100, GS 60) , c (100, GS 0), a (200): ranks
    d 1, b 2, c 3, a 4. Flat: d 0, c 100, b 150 (GS 60), a 150 (GS 50):
    d 1, c 2, b 3, a 4. Shifts 0, 1, 1, 0: max 1. Spearman on (a, b, c, d)
    = (4, 2, 3, 1) vs (4, 3, 2, 1): d = (0, 1, 1, 0), rho = 0.8. Top three
    {d, b, c} both times; bottom three {b, c, a} both times.
    """
    rows = [
        mix("a", 6, "am_peak", 10),
        mix("a", 10, "inter_peak", 10),
        mix("b", 6, "inter_peak", 10),
        mix("b", 10, "am_peak", 10),
        mix("c", 9, "inter_peak", 20),
        mix("d", 10, "inter_peak", 20),
    ]
    base = indexes_under(rows, BASE)
    assert base == pytest.approx({"a": 200.0, "b": 100.0, "c": 100.0, "d": 0.0})
    gs = {"a": 50.0, "b": 60.0, "c": 0.0, "d": 100.0}
    ranked = [RankedLine(k, base[k], gs[k]) for k in "abcd"]
    (result,) = week_sensitivity(ranked, rows, [FLAT])
    assert result.spearman_rho == pytest.approx(0.8)
    assert result.max_rank_shift == 1
    assert not result.top_three_changed and not result.bottom_three_changed
    (same,) = week_sensitivity(ranked, rows, [BASE])
    assert same.spearman_rho == pytest.approx(1.0) and same.max_rank_shift == 0


def test_base_weights_rebuild_the_published_index(marts: RecordedMarts) -> None:
    """Re-weighting rpt_weekly_status_mix with the seeds gives fct_line_weekly's index."""
    base, _ = load_weight_sets(TRANSFORM / "seeds")
    rebuilt = indexes_under([m for m in marts.status_mix() if m.week_start == WEEK], base)
    weekly = [w for w in marts.weekly() if w.week_start == WEEK and w.disruption_index is not None]
    assert weekly
    for row in weekly:
        assert rebuilt[row.line_id] == pytest.approx(row.disruption_index)


def test_methodology_carries_sensitivity_per_week(marts: RecordedMarts) -> None:
    _league, outputs = export.build_outputs(marts, WEEK, STAMP)
    methodology = next(o.document for o in outputs if o.path == "meta/methodology.json")
    sensitivity = methodology["sensitivity"]
    assert [s["weight_set"] for s in sensitivity["weight_sets"]] == [
        "flat_time",
        "linear_severity",
        "peak_heavy",
    ]
    (week,) = sensitivity["weeks"]
    assert week["iso_week"] == "2026-W45"
    assert [r["weight_set"] for r in week["results"]] == [
        "flat_time",
        "linear_severity",
        "peak_heavy",
    ]
    for result in week["results"]:
        assert -1 <= result["spearman_rho"] <= 1


def test_sensitivity_without_status_mix_reports_no_results() -> None:
    doc = sensitivity_document([(WEEK, "2026-W45", [RankedLine("a", 1.0, 90.0)])], [], [FLAT])
    assert doc["weeks"] == [{"iso_week": "2026-W45", "lines_ranked": 1, "results": []}]


def test_sensitivity_skips_a_week_missing_a_ranked_line() -> None:
    """A ranked line with no status mix rows is never re-scored as perfect service."""
    rows = [mix("a", 9, "inter_peak", 10)]
    ranked = [RankedLine("a", 100.0, 0.0), RankedLine("b", 0.0, 100.0)]
    assert week_sensitivity(ranked, rows, [FLAT]) == []


def test_a_week_lost_entirely_to_strikes_parses_with_no_completeness() -> None:
    cells = {
        "line_id": "waterloo-city",
        "week_start": "2026-11-23",
        "strike_days": "5",
        "week_has_industrial_action": "true",
        "scheduled_minutes": "0",
        "observed_minutes": "0",
        "disruption_index": None,
        "good_service_pct": None,
        "completeness": None,
        "is_provisional": "true",
        "unranked_reason": "no_scored_minutes",
        "disruption_rank": None,
    }
    row = parse_strikes_excluded(cells)
    assert row.completeness is None and row.disruption_rank is None


def test_a_division_month_not_built_yet_is_pending() -> None:
    state = division_state(date(2026, 12, 1), SEEDING, [], complete_weeks_available=9)
    doc = divisions_document(state, LINES, STAMP)
    validate(doc, "division")
    assert doc["status"] == "pending"
    # Once seeded, the count comes from dbt's seeding row.
    assert doc["complete_weeks_available"] == 2


def test_division_schema_ties_status_to_content() -> None:
    seeded = divisions_document(
        division_state(date(2026, 11, 1), SEEDING, [], complete_weeks_available=2), LINES, STAMP
    )
    with pytest.raises(ValidationError):
        validate({**seeded, "seeding": None}, "division")
    with pytest.raises(ValidationError):
        validate({**seeded, "divisions": [{"division": 1, "name": "x", "lines": []}]}, "division")
    rows = [month_row("victoria", 1, 1, "stays", complete=True)]
    playing = divisions_document(
        division_state(date(2026, 12, 1), SEEDING, rows, complete_weeks_available=2), LINES, STAMP
    )
    validate(playing, "division")
    first_line = {**playing["divisions"][0]["lines"][0], "rank": None}
    broken = {**playing, "divisions": [{**playing["divisions"][0], "lines": [first_line]}]}
    with pytest.raises(ValidationError):
        validate(broken, "division")
