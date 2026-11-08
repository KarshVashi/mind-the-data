"""Properties of the arrival inference reference on synthetic boards (PLAN 7.2).

A synthetic board is built from known trains: each has a true arrival time,
is listed from `HORIZON` seconds before it until it has dwelt at the
platform, and is predicted with a small error. Polls come about once a
minute. The properties say what inference must recover from such boards and
what noise (duplicates, phantoms, failed polls, input order) must not change.
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
MIN_HEADWAY = 120  # two trains never share a platform closer than this
PARAMS = InferenceParams()


@dataclass(frozen=True)
class Board:
    arrivals: list[datetime]
    polls: list[BoardPoll]
    predictions: list[Prediction]


@st.composite
def boards(draw: st.DrawFn) -> Board:
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
    predictions: list[Prediction] = []
    for n, arrival in enumerate(true_arrivals):
        dwell = draw(st.integers(0, 30))
        for poll in polls:
            if (
                not arrival - timedelta(seconds=HORIZON)
                <= poll.polled_at
                < arrival + timedelta(seconds=dwell)
            ):
                continue
            eta = arrival + timedelta(seconds=draw(st.integers(-NOISE, NOISE)))
            prediction_id = f"t{n}" if stable_ids else f"t{n}-{poll.poll_id}"
            predictions.append(_prediction(poll, "Platform 1", prediction_id, eta))
    return Board(true_arrivals, polls, predictions)


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


def _within_noise(inferred: list[datetime], true: list[datetime]) -> bool:
    """Each inferred arrival is within NOISE of a distinct true arrival, in order."""
    remaining = iter(true)
    for time in inferred:
        for candidate in remaining:
            if abs((time - candidate).total_seconds()) <= NOISE:
                break
        else:
            return False
    return True


@given(boards())
def test_every_train_is_found_once_within_the_prediction_error(board: Board) -> None:
    arrivals = infer_arrivals(board.predictions, board.polls)
    assert len(arrivals) == len(board.arrivals)
    for inferred, true in zip(_times(arrivals), board.arrivals, strict=True):
        assert abs((inferred - true).total_seconds()) <= NOISE


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
    clear = PARAMS.match_tolerance_seconds + NOISE + PARAMS.duplicate_tolerance_seconds
    if any(abs((eta - t).total_seconds()) <= clear for t in board.arrivals):
        return
    phantom = _prediction(poll, "Platform 1", "phantom", eta)
    before = infer_arrivals(board.predictions, board.polls)
    assert infer_arrivals([*board.predictions, phantom], board.polls) == before


@given(boards(), st.data())
@settings(max_examples=200)
def test_failed_polls_never_invent_arrivals(board: Board, data: st.DataObject) -> None:
    """With polls lost, fewer trains may be seen to arrive, but every arrival
    still matches a real train: a gap is never filled with a made-up one."""
    kept = data.draw(st.lists(st.booleans(), min_size=len(board.polls), max_size=len(board.polls)))
    polls = [p for p, keep in zip(board.polls, kept, strict=True) if keep]
    listed = {p.poll_id for p in polls}
    predictions = [p for p in board.predictions if p.poll_id in listed]
    inferred = _times(infer_arrivals(predictions, polls))
    assert len(inferred) <= len(board.arrivals)
    assert _within_noise(inferred, board.arrivals)


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
