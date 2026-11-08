"""Arrival inference from successive arrival boards (PLAN 7.2).

This is the reference implementation the SQL model ``int_inferred_arrivals``
is checked against: both read the same rows (``stg_arrivals`` predictions and
the successful arrivals polls from ``stg_polls``) and must return the same
arrivals. Every step is defined so that it can be written set-based in SQL,
without recursion:

1. **Boards.** A board is one platform of one station request in one poll:
   ``(request_path, naptan_id, line_id, platform_name, direction)``. A train
   is followed only within its board (PLAN 7.2, "for each platform and
   direction").
2. **Duplicates.** Within one board in one poll, predictions are ordered by
   ``(expected_arrival, prediction_id)``. A prediction whose expected arrival
   is within ``duplicate_tolerance_seconds`` of the one before it is the same
   train listed twice and is dropped (DECISIONS.md D25).
3. **Linking.** A prediction in poll *k* and one in the station's next
   successful poll *k+1* are candidates when their expected arrivals are
   within ``match_tolerance_seconds`` (PLAN 7.2 rule 1), or, on a line listed
   in ``vehicle_id_lines``, when both carry the same ``vehicle_id``. Each
   prediction picks its nearest candidate; a pair links only when the choice
   is mutual, so links are one to one. A chain of links is one train.
4. **Arrival.** A train's last observation (one with no link to the next
   poll) is an arrival when the station's next successful poll exists and the
   observation's ``time_to_station`` is at most ``arrival_threshold_seconds``
   (rule 2, "absent in poll t+1"), or when no later poll exists and it shows
   ``time_to_station`` = 0 (rule 2, "or shows 0"). The arrival time is that
   observation's ``expected_arrival``, the last one seen for the train
   (rule 3). A train seen once with more than the threshold to go is never an
   arrival (rule 4, phantoms) (DECISIONS.md D26).

"Next poll" means the station's next *successful* poll. When polls failed in
between, the arrival is still inferred, and ``confirmed_at`` shows how long
after ``last_seen_at`` the absence was seen, so a headway that spans a
collection gap can be dropped downstream rather than read as a long gap.

All times are UTC. Nothing here knows about London time or schedules.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

BoardKey = tuple[str, str, str, str | None, str | None]
"""(request_path, naptan_id, line_id, platform_name, direction)."""

ArrivalRule = Literal["left_board", "at_platform"]


@dataclass(frozen=True)
class InferenceParams:
    """Thresholds of the inference rule. Defaults follow PLAN 7.2 and DECISIONS.md D25 to D28."""

    match_tolerance_seconds: float = 45.0
    duplicate_tolerance_seconds: float = 15.0
    arrival_threshold_seconds: int = 120
    # Lines whose vehicle_id is stable per train. Empty until the week-one
    # data review establishes which lines qualify (D27).
    vehicle_id_lines: frozenset[str] = field(default_factory=frozenset)


DEFAULT_PARAMS = InferenceParams()


@dataclass(frozen=True)
class BoardPoll:
    """One successful arrivals request for one station (``stg_polls``, feed arrivals)."""

    poll_id: str
    request_path: str
    polled_at: datetime


@dataclass(frozen=True)
class Prediction:
    """One row of ``stg_arrivals``, reduced to what inference reads."""

    poll_id: str
    request_path: str
    naptan_id: str
    line_id: str
    platform_name: str | None
    direction: str | None
    prediction_id: str
    vehicle_id: str | None
    expected_arrival: datetime
    time_to_station: int

    @property
    def board(self) -> BoardKey:
        return (
            self.request_path,
            self.naptan_id,
            self.line_id,
            self.platform_name,
            self.direction,
        )


@dataclass(frozen=True)
class InferredArrival:
    """One inferred train arrival at one board."""

    request_path: str
    naptan_id: str
    line_id: str
    platform_name: str | None
    direction: str | None
    arrival_time: datetime
    rule: ArrivalRule
    last_poll_id: str
    last_prediction_id: str
    last_time_to_station: int
    vehicle_id: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    # Poll time of the station's next successful poll, in which the train was
    # absent; None for an at_platform arrival at the end of the input.
    confirmed_at: datetime | None
    observations: int


@dataclass(frozen=True)
class _Observation:
    poll_index: int
    prediction: Prediction

    @property
    def order(self) -> tuple[datetime, str]:
        return (self.prediction.expected_arrival, self.prediction.prediction_id)


@dataclass
class _Train:
    observations: list[_Observation]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"naive datetime {value!r}; inference works in UTC only")
    return value.astimezone(UTC)


def drop_duplicates(
    predictions: Sequence[Prediction], tolerance_seconds: float
) -> list[Prediction]:
    """One board in one poll, without the second listing of the same train.

    Ordered by (expected_arrival, prediction_id); a prediction within
    ``tolerance_seconds`` of the one before it is dropped. Comparing with the
    previous row, not the previous kept row, keeps this a window function in
    SQL (``lag``).
    """
    ordered = sorted(predictions, key=lambda p: (p.expected_arrival, p.prediction_id))
    kept: list[Prediction] = []
    for index, prediction in enumerate(ordered):
        if index > 0:
            gap = (
                prediction.expected_arrival - ordered[index - 1].expected_arrival
            ).total_seconds()
            if gap <= tolerance_seconds:
                continue
        kept.append(prediction)
    return kept


def _distance(earlier: Prediction, later: Prediction, params: InferenceParams) -> float | None:
    """How far apart two observations are as the same train; None if they cannot be."""
    if (
        earlier.line_id in params.vehicle_id_lines
        and earlier.vehicle_id is not None
        and later.vehicle_id is not None
    ):
        return 0.0 if earlier.vehicle_id == later.vehicle_id else None
    gap = abs((later.expected_arrival - earlier.expected_arrival).total_seconds())
    return gap if gap <= params.match_tolerance_seconds else None


def link_polls(
    earlier: Sequence[Prediction],
    later: Sequence[Prediction],
    params: InferenceParams = DEFAULT_PARAMS,
) -> dict[int, int]:
    """Mutual nearest-candidate links between one board's two consecutive polls.

    Returns {index in ``earlier``: index in ``later``}. Ties go to the earlier
    expected arrival, then the lower prediction id, so the result does not
    depend on input order.
    """
    best_later: dict[int, int] = {}
    best_earlier: dict[int, int] = {}
    for i, a in enumerate(earlier):
        options = [
            (d, b.expected_arrival, b.prediction_id, j)
            for j, b in enumerate(later)
            if (d := _distance(a, b, params)) is not None
        ]
        if options:
            best_later[i] = min(options)[3]
    for j, b in enumerate(later):
        options = [
            (d, a.expected_arrival, a.prediction_id, i)
            for i, a in enumerate(earlier)
            if (d := _distance(a, b, params)) is not None
        ]
        if options:
            best_earlier[j] = min(options)[3]
    return {i: j for i, j in best_later.items() if best_earlier.get(j) == i}


def _station_polls(polls: Iterable[BoardPoll]) -> dict[str, list[BoardPoll]]:
    by_station: dict[str, dict[str, BoardPoll]] = defaultdict(dict)
    for poll in polls:
        by_station[poll.request_path][poll.poll_id] = BoardPoll(
            poll.poll_id, poll.request_path, _utc(poll.polled_at)
        )
    return {
        station: sorted(found.values(), key=lambda p: (p.polled_at, p.poll_id))
        for station, found in by_station.items()
    }


def _trains_on_board(board_polls: list[list[Prediction]], params: InferenceParams) -> list[_Train]:
    """Chain one board's observations, poll by poll, into trains."""
    trains: list[_Train] = []
    previous: list[tuple[Prediction, _Train]] = []
    for poll_index, board in enumerate(board_polls):
        current = drop_duplicates(board, params.duplicate_tolerance_seconds)
        links = link_polls([p for p, _ in previous], current, params)
        continued = {j: previous[i][1] for i, j in links.items()}
        now: list[tuple[Prediction, _Train]] = []
        for j, prediction in enumerate(current):
            observation = _Observation(poll_index, prediction)
            train = continued.get(j)
            if train is None:
                train = _Train([observation])
                trains.append(train)
            else:
                train.observations.append(observation)
            now.append((prediction, train))
        previous = now
    return trains


def _arrival(
    train: _Train, station_polls: list[BoardPoll], params: InferenceParams
) -> InferredArrival | None:
    last = train.observations[-1]
    prediction = last.prediction
    next_index = last.poll_index + 1
    confirmed_at: datetime | None
    if next_index < len(station_polls):
        if prediction.time_to_station > params.arrival_threshold_seconds:
            return None
        rule: ArrivalRule = "at_platform" if prediction.time_to_station == 0 else "left_board"
        confirmed_at = station_polls[next_index].polled_at
    else:
        if prediction.time_to_station != 0:
            return None
        rule = "at_platform"
        confirmed_at = None
    vehicle_ids = [o.prediction.vehicle_id for o in train.observations if o.prediction.vehicle_id]
    return InferredArrival(
        request_path=prediction.request_path,
        naptan_id=prediction.naptan_id,
        line_id=prediction.line_id,
        platform_name=prediction.platform_name,
        direction=prediction.direction,
        arrival_time=prediction.expected_arrival,
        rule=rule,
        last_poll_id=prediction.poll_id,
        last_prediction_id=prediction.prediction_id,
        last_time_to_station=prediction.time_to_station,
        vehicle_id=vehicle_ids[-1] if vehicle_ids else None,
        first_seen_at=station_polls[train.observations[0].poll_index].polled_at,
        last_seen_at=station_polls[last.poll_index].polled_at,
        confirmed_at=confirmed_at,
        observations=len(train.observations),
    )


def _board_sort_key(board: BoardKey) -> tuple[str, str, str, str, str]:
    request_path, naptan_id, line_id, platform_name, direction = board
    return (request_path, naptan_id, line_id, platform_name or "", direction or "")


def infer_arrivals(
    predictions: Iterable[Prediction],
    polls: Iterable[BoardPoll],
    params: InferenceParams = DEFAULT_PARAMS,
) -> list[InferredArrival]:
    """Every arrival the boards show, ordered by board then arrival time.

    ``polls`` must hold every successful arrivals poll of each station in the
    window, including polls whose board was empty: absence from a poll is
    what ends a train, so a poll that is not listed cannot end one. A
    prediction from a poll that is not listed is an error.
    """
    station_polls = _station_polls(polls)
    poll_index = {
        (poll.request_path, poll.poll_id): index
        for station in station_polls.values()
        for index, poll in enumerate(station)
    }

    boards: dict[BoardKey, dict[int, list[Prediction]]] = defaultdict(lambda: defaultdict(list))
    for prediction in predictions:
        key = (prediction.request_path, prediction.poll_id)
        if key not in poll_index:
            raise ValueError(
                f"prediction {prediction.prediction_id} is from poll {prediction.poll_id} "
                f"at {prediction.request_path}, which is not a listed successful poll"
            )
        normalised = replace(prediction, expected_arrival=_utc(prediction.expected_arrival))
        boards[prediction.board][poll_index[key]].append(normalised)

    arrivals: list[InferredArrival] = []
    for board in sorted(boards, key=_board_sort_key):
        polls_here = station_polls[board[0]]
        per_poll = [boards[board].get(index, []) for index in range(len(polls_here))]
        for train in _trains_on_board(per_poll, params):
            arrival = _arrival(train, polls_here, params)
            if arrival is not None:
                arrivals.append(arrival)
    arrivals.sort(
        key=lambda a: (
            _board_sort_key((a.request_path, a.naptan_id, a.line_id, a.platform_name, a.direction)),
            a.arrival_time,
            a.last_prediction_id,
        )
    )
    return arrivals


# ---- Plain rows ---------------------------------------------------------------


def _text(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _optional_text(row: Mapping[str, object], name: str) -> str | None:
    value = row.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null, got {value!r}")
    return value


def _timestamp(row: Mapping[str, object], name: str) -> datetime:
    value = row[name]
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        # Athena prints timestamps as 'YYYY-MM-DD HH:MM:SS.fff' with no zone;
        # stg_ columns are UTC by contract.
        parsed = datetime.fromisoformat(value)
    else:
        raise TypeError(f"{name} must be a timestamp, got {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _integer(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TypeError(f"{name} must be an integer, got {value!r}")
    return int(value)


def prediction_from_row(row: Mapping[str, object]) -> Prediction:
    """A ``stg_arrivals`` row (column names as in the model) as a Prediction."""
    return Prediction(
        poll_id=_text(row, "poll_id"),
        request_path=_text(row, "request_path"),
        naptan_id=_text(row, "naptan_id"),
        line_id=_text(row, "line_id"),
        platform_name=_optional_text(row, "platform_name"),
        direction=_optional_text(row, "direction"),
        prediction_id=_text(row, "prediction_id"),
        vehicle_id=_optional_text(row, "vehicle_id"),
        expected_arrival=_timestamp(row, "expected_arrival"),
        time_to_station=_integer(row, "time_to_station"),
    )


def poll_from_row(row: Mapping[str, object]) -> BoardPoll:
    """A successful arrivals ``stg_polls`` row as a BoardPoll."""
    return BoardPoll(
        poll_id=_text(row, "poll_id"),
        request_path=_text(row, "request_path"),
        polled_at=_timestamp(row, "polled_at"),
    )


ARRIVAL_COLUMNS = (
    "request_path",
    "naptan_id",
    "line_id",
    "platform_name",
    "direction",
    "arrival_time",
    "rule",
    "last_poll_id",
    "last_prediction_id",
    "last_time_to_station",
)
"""The columns the SQL model must reproduce exactly, in comparison order."""


def infer_arrivals_from_rows(
    prediction_rows: Iterable[Mapping[str, object]],
    poll_rows: Iterable[Mapping[str, object]],
    params: InferenceParams = DEFAULT_PARAMS,
) -> list[dict[str, object]]:
    """Plain rows in, plain rows out: the entry point for the SQL comparison.

    ``prediction_rows`` are ``stg_arrivals`` rows; ``poll_rows`` are the
    successful arrivals rows of ``stg_polls`` for the same window. Returns
    one dict per arrival with the ``ARRIVAL_COLUMNS``, sorted.
    """
    arrivals = infer_arrivals(
        (prediction_from_row(row) for row in prediction_rows),
        (poll_from_row(row) for row in poll_rows),
        params,
    )
    return [{column: getattr(a, column) for column in ARRIVAL_COLUMNS} for a in arrivals]
