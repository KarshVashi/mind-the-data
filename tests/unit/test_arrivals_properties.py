"""Properties of the arrival inference reference on synthetic boards (PLAN 7.2).

A synthetic board is built from known trains: each has a true arrival time,
is listed from `HORIZON` seconds before it until it has dwelt at the
platform, and is predicted with a small error. While a train stands at the
platform it shows 0 s to go and its expected arrival is the poll time, as
TfL's is (timestamp plus time to station). Polls come about once a minute.
The properties say what inference must recover from such boards and what
noise (duplicates, phantoms, failed polls, a train dropping off the board,
input order) must not change.

Two trains are never closer than `MIN_HEADWAY`, which is more than the
longest dwell plus a poll gap: closer than that, a standing train and the
next one at the platform cannot be told apart without vehicle ids (D26).
"""

import itertools
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from mind_the_data.analysis.arrivals import (
    BoardPoll,
    InferenceParams,
    InferredArrival,
    Prediction,
    drop_duplicates,
    infer_arrivals,
)

T0 = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
STATION = "/StopPoint/940GZZLUVIC/Arrivals"
HORIZON = 600  # seconds a train is listed before it is due
NOISE = 10  # largest prediction error, seconds
MAX_DWELL = 60  # longest stand at the platform, seconds
MIN_HEADWAY = 150  # two trains never share a platform closer than this
PARAMS = InferenceParams()


@dataclass(frozen=True)
class Board:
    arrivals: list[datetime]
    polls: list[BoardPoll]
    predictions: list[Prediction]
    # Each train's sightings, in poll order.
    trains: list[list[Prediction]]


@st.composite
def boards(draw: st.DrawFn, max_dwell: int = MAX_DWELL) -> Board:
    headways = draw(st.lists(st.integers(MIN_HEADWAY, 900), min_size=1, max_size=8))
    first = draw(st.integers(0, 300))
    offsets = [first + sum(headways[:i]) for i in range(len(headways))]
    true_arrivals = [T0 + timedelta(seconds=HORIZON + o) for o in offsets]

    end = true_arrivals[-1] + timedelta(seconds=300)
    poll_times: list[datetime] = []
    moment = T0
    while moment < end:
        poll_times.append(moment)
        moment += timedelta(seconds=60 + draw(st.integers(-5, 5)))
    polls = [BoardPoll(f"p{i:04d}", STATION, t) for i, t in enumerate(poll_times)]

    stable_ids = draw(st.booleans())
    trains: list[list[Prediction]] = []
    for n, arrival in enumerate(true_arrivals):
        dwell = draw(st.integers(0, max_dwell))
        sightings: list[Prediction] = []
        for poll in polls:
            if (
                not arrival - timedelta(seconds=HORIZON)
                <= poll.polled_at
                < arrival + timedelta(seconds=dwell)
            ):
                continue
            if poll.polled_at >= arrival:
                eta = poll.polled_at
            else:
                eta = arrival + timedelta(seconds=draw(st.integers(-NOISE, NOISE)))
            prediction_id = f"t{n}" if stable_ids else f"t{n}-{poll.poll_id}"
            sightings.append(_prediction(poll, "Platform 1", prediction_id, eta))
        trains.append(sightings)
    predictions = [p for sightings in trains for p in sightings]
    return Board(true_arrivals, polls, predictions, trains)


def _prediction(poll: BoardPoll, platform: str, prediction_id: str, eta: datetime) -> Prediction:
    return Prediction(
        poll_id=poll.poll_id,
        request_path=poll.request_path,
        naptan_id="940GZZLUVIC",
        line_id="victoria",
        platform_name=platform,
        direction="inbound",
        prediction_id=prediction_id,
        vehicle_id=None,
        expected_arrival=eta,
        time_to_station=max(0, round((eta - poll.polled_at).total_seconds())),
    )


def _times(arrivals: list[InferredArrival]) -> list[datetime]:
    return [a.arrival_time for a in arrivals]


def _close(inferred: datetime, true: datetime) -> bool:
    """The arrival time is the last expected arrival seen: up to NOISE early,
    or as late as the last poll the train stood at the platform."""
    return -NOISE <= (inferred - true).total_seconds() <= MAX_DWELL


def _matches_distinct(inferred: list[datetime], true: list[datetime]) -> bool:
    """Each inferred arrival is close to a distinct true arrival, in order."""
    remaining = iter(true)
    for time in inferred:
        for candidate in remaining:
            if _close(time, candidate):
                break
        else:
            return False
    return True


@given(boards())
def test_every_train_is_found_once(board: Board) -> None:
    arrivals = infer_arrivals(board.predictions, board.polls)
    assert len(arrivals) == len(board.arrivals)
    for inferred, true in zip(_times(arrivals), board.arrivals, strict=True):
        assert _close(inferred, true)


@given(boards(), st.data())
def test_dropping_off_the_board_far_out_changes_nothing(board: Board, data: st.DataObject) -> None:
    """A train missing from single polls while still more than 120 s out
    (board flicker) is still one arrival. Flicker close to the platform is
    covered by a hand-computed case."""
    kept: list[Prediction] = []
    for sightings in board.trains:
        drops = data.draw(st.lists(st.booleans(), min_size=len(sightings), max_size=len(sightings)))
        previous_kept = True
        for i, sighting in enumerate(sightings):
            drop = (
                drops[i]
                and 0 < i < len(sightings) - 1
                and previous_kept
                and sightings[i - 1].time_to_station > PARAMS.arrival_threshold_seconds
            )
            if not drop:
                kept.append(sighting)
            previous_kept = not drop
    assert _times(infer_arrivals(kept, board.polls)) == _times(
        infer_arrivals(board.predictions, board.polls)
    )


@given(boards())
def test_arrivals_obey_the_rule(board: Board) -> None:
    observed = {p.expected_arrival for p in board.predictions}
    for arrival in infer_arrivals(board.predictions, board.polls):
        assert arrival.arrival_time in observed
        assert arrival.last_time_to_station <= PARAMS.arrival_threshold_seconds
        if arrival.confirmed_at is None:
            assert arrival.last_time_to_station == 0
        else:
            assert arrival.last_seen_at < arrival.confirmed_at
        assert arrival.first_seen_at <= arrival.last_seen_at


@given(boards(), st.randoms(use_true_random=False))
def test_input_order_does_not_matter(board: Board, rng: random.Random) -> None:
    predictions = list(board.predictions)
    polls = list(board.polls)
    rng.shuffle(predictions)
    rng.shuffle(polls)
    assert infer_arrivals(predictions, polls) == infer_arrivals(board.predictions, board.polls)


@given(boards(), st.data())
def test_duplicate_listings_change_nothing(board: Board, data: st.DataObject) -> None:
    """Every sighting listed again under another id, up to 15 s later."""
    shift = data.draw(st.integers(0, int(PARAMS.duplicate_tolerance_seconds)))
    copies = [
        replace(
            p,
            prediction_id=f"dup-{p.prediction_id}",
            expected_arrival=p.expected_arrival + timedelta(seconds=shift),
            time_to_station=p.time_to_station + shift,
        )
        for p in board.predictions
    ]
    before = _times(infer_arrivals(board.predictions, board.polls))
    after = _times(infer_arrivals(board.predictions + copies, board.polls))
    assert after == before


@given(boards(), st.data())
def test_phantoms_change_nothing(board: Board, data: st.DataObject) -> None:
    """A train listed once, more than 120 s out and nowhere near a real train."""
    poll = data.draw(st.sampled_from(board.polls))
    lead = data.draw(st.integers(PARAMS.arrival_threshold_seconds + 1, 3600))
    eta = poll.polled_at + timedelta(seconds=lead)
    # Far enough from every real sighting that no link, moving or standing,
    # can reach it.
    clear = 2 * PARAMS.match_tolerance_seconds + 65 + NOISE + MAX_DWELL
    if any(abs((eta - t).total_seconds()) <= clear for t in board.arrivals):
        return
    phantom = _prediction(poll, "Platform 1", "phantom", eta)
    before = infer_arrivals(board.predictions, board.polls)
    assert infer_arrivals([*board.predictions, phantom], board.polls) == before


@given(boards(max_dwell=30), st.data())
@settings(max_examples=200)
def test_failed_polls_never_invent_arrivals(board: Board, data: st.DataObject) -> None:
    """With polls lost, fewer trains may be seen to arrive, but every arrival
    still matches a real train: a gap is never filled with a made-up one.
    Dwells are kept to 30 s here: a train last seen 45 to 120 s out and next
    seen still standing at the platform after a lost poll is two sightings
    neither rule links, and counts twice (D28)."""
    kept = data.draw(st.lists(st.booleans(), min_size=len(board.polls), max_size=len(board.polls)))
    polls = [p for p, keep in zip(board.polls, kept, strict=True) if keep]
    listed = {p.poll_id for p in polls}
    predictions = [p for p in board.predictions if p.poll_id in listed]
    inferred = _times(infer_arrivals(predictions, polls))
    assert len(inferred) <= len(board.arrivals)
    assert _matches_distinct(inferred, board.arrivals)


@given(boards())
def test_platforms_are_inferred_independently(board: Board) -> None:
    """The same trains on a second platform of the same station board: the
    result is the union of each platform's own result."""
    other = [replace(p, platform_name="Platform 2") for p in board.predictions]
    alone_one = infer_arrivals(board.predictions, board.polls)
    alone_two = infer_arrivals(other, board.polls)
    together = infer_arrivals(board.predictions + other, board.polls)
    assert sorted(together, key=repr) == sorted(alone_one + alone_two, key=repr)
    assert len(together) == 2 * len(alone_one)


@given(
    st.lists(st.integers(0, 600), max_size=12),
    st.floats(0, 60, allow_nan=False),
)
def test_drop_duplicates_keeps_rows_more_than_the_tolerance_apart(
    offsets: list[int], tolerance: float
) -> None:
    poll = BoardPoll("p0", STATION, T0)
    rows = [
        _prediction(poll, "Platform 1", f"id{i}", T0 + timedelta(seconds=o))
        for i, o in enumerate(offsets)
    ]
    kept = drop_duplicates(rows, tolerance)
    times = [p.expected_arrival for p in kept]
    assert times == sorted(times)
    assert all((b - a).total_seconds() > tolerance for a, b in itertools.pairwise(times))
    assert drop_duplicates(kept, tolerance) == kept
    if rows:
        assert kept[0].expected_arrival == min(p.expected_arrival for p in rows)
