"""Hand-computed cases for the arrival inference reference (PLAN 7.2).

Every expected value is worked out in the test's docstring from the rule in
mind_the_data.analysis.arrivals, not copied from a run. Polls are a minute
apart from 08:00:00 UTC on 2026-11-05 unless a test says otherwise.
"""

from datetime import UTC, datetime, timedelta

import pytest

from mind_the_data.analysis.arrivals import (
    BoardPoll,
    InferenceParams,
    Prediction,
    drop_duplicates,
    infer_arrivals,
    infer_arrivals_from_rows,
    link_polls,
)

T0 = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
STATION = "/StopPoint/940GZZLUOXC/Arrivals"
PLATFORM = "Northbound - Platform 5"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def polls(*minutes: int, station: str = STATION) -> list[BoardPoll]:
    return [BoardPoll(f"p{m}", station, at(60 * m)) for m in minutes]


def seen(
    minute: int,
    prediction_id: str,
    eta_seconds: float,
    *,
    platform: str = PLATFORM,
    vehicle_id: str | None = None,
    line_id: str = "victoria",
) -> Prediction:
    """A prediction on the board at poll `minute`, due `eta_seconds` after 08:00."""
    eta = at(eta_seconds)
    return Prediction(
        poll_id=f"p{minute}",
        request_path=STATION,
        naptan_id="940GZZLUOXC",
        line_id=line_id,
        platform_name=platform,
        direction="outbound",
        prediction_id=prediction_id,
        vehicle_id=vehicle_id,
        expected_arrival=eta,
        time_to_station=max(0, round(eta_seconds - 60 * minute)),
    )


def test_train_that_leaves_the_board_arrives_at_its_last_expected_time() -> None:
    """Seen at 08:00, 08:01, 08:02 due 08:02:30, 08:02:40, 08:02:35 (tts 150,
    100, 35). Each step moves under 45 s, so it is one train. Absent at 08:03
    with 35 s to go at its last sighting: arrived at 08:02:35, the last
    expected arrival."""
    rows = [seen(0, "a", 150), seen(1, "a", 160), seen(2, "a", 155)]
    [arrival] = infer_arrivals(rows, polls(0, 1, 2, 3))
    assert arrival.arrival_time == at(155)
    assert arrival.rule == "left_board"
    assert arrival.observations == 3
    assert arrival.first_seen_at == at(0)
    assert arrival.last_seen_at == at(120)
    assert arrival.confirmed_at == at(180)
    assert arrival.last_time_to_station == 35


def test_train_that_vanishes_far_out_did_not_arrive() -> None:
    """Due 08:06:40 (tts 400, then 340), gone at 08:02: it left the board more
    than 120 s out, so it is not an arrival."""
    rows = [seen(0, "a", 400), seen(1, "a", 400)]
    assert infer_arrivals(rows, polls(0, 1, 2)) == []


def test_threshold_is_inclusive() -> None:
    """Last seen with exactly 120 s to go, then gone: arrived. 121 s: not."""
    assert len(infer_arrivals([seen(0, "a", 120)], polls(0, 1))) == 1
    assert infer_arrivals([seen(0, "a", 121)], polls(0, 1)) == []


def test_phantom_seen_once_far_out_is_discarded_but_a_close_sighting_counts() -> None:
    """Rule 4: one sighting at 600 s out, then gone: a phantom. One sighting at
    90 s out, then gone: a train that came into range late, an arrival."""
    rows = [seen(1, "phantom", 660), seen(1, "late", 150)]
    [arrival] = infer_arrivals(rows, polls(0, 1, 2))
    assert arrival.last_prediction_id == "late"
    assert arrival.observations == 1
    assert arrival.arrival_time == at(150)


def test_zero_to_go_at_the_last_poll_is_an_arrival_without_confirmation() -> None:
    """The window ends at 08:01. Train a shows 0 s to go there: arrived (rule 2,
    "or shows 0"), with no absence to confirm it. Train b shows 30 s to go at
    the last poll: nothing says whether it arrived, so it is left out."""
    rows = [seen(0, "a", 70), seen(1, "a", 55), seen(1, "b", 90)]
    [arrival] = infer_arrivals(rows, polls(0, 1))
    assert arrival.last_prediction_id == "a"
    assert arrival.rule == "at_platform"
    assert arrival.confirmed_at is None
    assert arrival.arrival_time == at(55)


def test_train_dwelling_at_zero_is_one_arrival() -> None:
    """At the platform (0 s) at 08:01 and 08:02, gone at 08:03: one arrival, at
    the last expected time (08:01:20), rule at_platform."""
    rows = [seen(0, "a", 70), seen(1, "a", 50), seen(2, "a", 80)]
    [arrival] = infer_arrivals(rows, polls(0, 1, 2, 3))
    assert arrival.observations == 3
    assert arrival.rule == "at_platform"
    assert arrival.arrival_time == at(80)


def test_duplicate_listing_of_one_train_is_collapsed() -> None:
    """Two ids 10 s apart in each poll (within 15 s) are one train; the earlier
    expected arrival is kept (a at 08:01:30 then 08:01:35). Train c, 90 s
    behind, is a separate train. a is gone at 08:02 (35 s to go at 08:01),
    c at 08:03 (65 s to go at 08:02). Arrivals: a at 08:01:35, c at 08:03:05."""
    rows = [
        seen(0, "a", 90),
        seen(0, "b", 100),
        seen(0, "c", 180),
        seen(1, "b", 105),
        seen(1, "a", 95),
        seen(1, "c", 180),
        seen(2, "c", 185),
    ]
    arrivals = infer_arrivals(rows, polls(0, 1, 2, 3))
    assert [(a.last_prediction_id, a.arrival_time) for a in arrivals] == [
        ("a", at(95)),
        ("c", at(185)),
    ]


def test_absence_after_failed_polls_is_confirmed_by_the_next_success() -> None:
    """Polls at 08:02 and 08:03 failed, so the station's next successful poll
    after 08:01 is 08:04. The train (90 s to go at 08:01) is absent then:
    arrived at 08:02:30, confirmed 3 minutes after it was last seen."""
    rows = [seen(0, "a", 160), seen(1, "a", 150)]
    [arrival] = infer_arrivals(rows, polls(0, 1, 4))
    assert arrival.arrival_time == at(150)
    assert arrival.last_seen_at == at(60)
    assert arrival.confirmed_at == at(240)


def test_empty_board_ends_every_train() -> None:
    """The 08:01 poll succeeded with nothing on the board: both trains are
    absent there and both were within 120 s (60 s and 110 s to go)."""
    rows = [seen(0, "a", 60), seen(0, "b", 110)]
    arrivals = infer_arrivals(rows, polls(0, 1))
    assert [a.arrival_time for a in arrivals] == [at(60), at(110)]


def test_held_train_drifting_more_than_tolerance_splits() -> None:
    """A train held 90 s out: its expected arrival moves 60 s per poll, more
    than the 45 s tolerance, so each sighting is a separate train and each
    ends within 120 s. The literal rule infers three arrivals for one train
    (DECISIONS.md D26). With a trusted vehicle id it is one arrival."""
    rows = [
        seen(0, "a", 90, vehicle_id="201"),
        seen(1, "a", 150, vehicle_id="201"),
        seen(2, "a", 210, vehicle_id="201"),
    ]
    split = infer_arrivals(rows, polls(0, 1, 2, 3))
    assert [a.arrival_time for a in split] == [at(90), at(150), at(210)]

    trusted = InferenceParams(vehicle_id_lines=frozenset({"victoria"}))
    [arrival] = infer_arrivals(rows, polls(0, 1, 2, 3), trusted)
    assert arrival.arrival_time == at(210)
    assert arrival.vehicle_id == "201"
    assert arrival.observations == 3


def test_trusted_vehicle_ids_keep_two_close_trains_apart() -> None:
    """On a trusted line, different vehicle ids never link, even 20 s apart.
    Without a vehicle id the expected-arrival rule applies."""
    trusted = InferenceParams(vehicle_id_lines=frozenset({"victoria"}))
    earlier = [seen(0, "a", 100, vehicle_id="201")]
    assert link_polls(earlier, [seen(1, "b", 120, vehicle_id="202")], trusted) == {}
    assert link_polls(earlier, [seen(1, "b", 120)], trusted) == {0: 0}


def test_links_are_mutual_nearest() -> None:
    """Earlier poll: A due at 100 s, B at 140 s. Later poll: C at 120 s. A and
    B are both 20 s from C; the tie goes to the earlier expected arrival, so
    C links to A and B ends."""
    earlier = [seen(0, "A", 100), seen(0, "B", 140)]
    later = [seen(1, "C", 120)]
    assert link_polls(earlier, later) == {0: 0}


def test_boards_are_separate() -> None:
    """Two platforms with a train due at the same moment: two arrivals."""
    rows = [seen(0, "a", 60), seen(0, "b", 60, platform="Southbound - Platform 6")]
    arrivals = infer_arrivals(rows, polls(0, 1))
    assert {a.platform_name for a in arrivals} == {PLATFORM, "Southbound - Platform 6"}


def test_drop_duplicates_compares_with_the_previous_row() -> None:
    """08:01:00, :10, :20: the second is 10 s after the first and the third 10 s
    after the second, so both go, although the third is 20 s from the first
    kept one. Matches a SQL `lag` over (expected_arrival, prediction_id)."""
    rows = [seen(0, "x", 60), seen(0, "y", 70), seen(0, "z", 80)]
    assert [p.prediction_id for p in drop_duplicates(rows, 15)] == ["x"]


def test_prediction_from_an_unlisted_poll_is_an_error() -> None:
    with pytest.raises(ValueError, match="not a listed successful poll"):
        infer_arrivals([seen(2, "a", 150)], polls(0, 1))


def test_naive_timestamps_are_refused() -> None:
    with pytest.raises(ValueError, match="UTC only"):
        infer_arrivals([], [BoardPoll("p0", STATION, datetime(2026, 11, 5, 8, 0))])


def test_plain_rows_in_plain_rows_out() -> None:
    """Rows shaped like stg_arrivals and stg_polls, timestamps as Athena prints
    them (UTC, no zone). Train a: 150 s then 90 s to go, gone at 08:02."""
    poll_rows = [
        {"poll_id": "p0", "request_path": STATION, "polled_at": "2026-11-05 08:00:00.000"},
        {"poll_id": "p1", "request_path": STATION, "polled_at": "2026-11-05 08:01:00.000"},
        {"poll_id": "p2", "request_path": STATION, "polled_at": "2026-11-05 08:02:00.000"},
    ]
    base: dict[str, object] = {
        "request_path": STATION,
        "naptan_id": "940GZZLUOXC",
        "line_id": "victoria",
        "platform_name": PLATFORM,
        "direction": "",
        "prediction_id": "a",
        "vehicle_id": None,
    }
    prediction_rows: list[dict[str, object]] = [
        {**base, "poll_id": "p0", "expected_arrival": "2026-11-05 08:02:30.000",
         "time_to_station": 150},
        {**base, "poll_id": "p1", "expected_arrival": "2026-11-05 08:02:30.000",
         "time_to_station": "90"},
    ]  # fmt: skip
    assert infer_arrivals_from_rows(prediction_rows, poll_rows) == [
        {
            "request_path": STATION,
            "naptan_id": "940GZZLUOXC",
            "line_id": "victoria",
            "platform_name": PLATFORM,
            "direction": None,
            "arrival_time": at(150),
            "rule": "left_board",
            "last_poll_id": "p1",
            "last_prediction_id": "a",
            "last_time_to_station": 90,
        }
    ]
