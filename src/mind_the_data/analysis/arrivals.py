"""Arrival inference from successive arrival boards (PLAN 7.2).

This is the reference implementation the SQL model ``int_inferred_arrivals``
is checked against: both read the same rows (``int_arrivals_directed``
predictions, ``stg_arrivals`` with a missing direction filled (D65), and
the successful arrivals polls from ``stg_polls``) and must return the
same arrivals. The fill itself is checked by its dbt unit test, not here. The compared columns (``ARRIVAL_COLUMNS``) depend only on a
sighting, the station's next successful poll and the arrivals beside it on
the same board, so SQL can compute them set-based, without recursion. The
chain-derived fields of ``InferredArrival`` (``first_seen_at``,
``observations``, ``vehicle_id``) are diagnostics for Python only.

1. **Boards.** A board is one platform of one station request in one poll:
   ``(request_path, naptan_id, line_id, platform_name, direction)``. A train
   is followed only within its board (PLAN 7.2, "for each platform and
   direction").
2. **Duplicates.** Within one board in one poll, predictions are ordered by
   ``(expected_arrival, prediction_id)``. A prediction whose expected arrival
   is within ``duplicate_tolerance_seconds`` of the one before it is the same
   train listed twice and is dropped (DECISIONS.md D25).
3. **Linking.** A prediction in poll *k* and one in the station's next
   successful poll *k+1* are candidates when
   - their expected arrivals are within ``match_tolerance_seconds`` (PLAN 7.2
     rule 1): a moving train; or
   - the later is due now (0 s to go), the earlier at most the
     tolerance, and the expected arrival moved by the time between the polls,
     give or take the tolerance: a train standing at the platform. TfL's expected arrival is its timestamp plus
     ``time_to_station``, so a standing train pushes it on by a minute a
     minute (D26). Standing matches rank after every moving match. A train
     held short of the platform is not linked this way and can count more
     than once (a known bias, D26); or
   - on a line listed in ``vehicle_id_lines``, both carry the same
     ``vehicle_id`` and the expected arrival moved by no more than the poll
     gap plus the tolerance (D27). Placeholder ids (``000``, ``001``), which
     several trains share, count as no id.
   Each prediction picks its nearest candidate; a pair links only when the
   choice is mutual, so links are one to one. A chain of links is one train.
4. **Arrival.** A train's last observation (one with no link to the next
   poll) is an arrival when the station's next successful poll exists and the
   observation is at most ``arrival_threshold_seconds`` to go (rule 2,
   "absent in poll t+1"), or when no later poll exists and it is 0 s to go
   (rule 2, "or shows 0"). The arrival time is that
   observation's ``expected_arrival``, the last one seen for the train
   (rule 3). A train seen once with more than the threshold to go is never an
   arrival (rule 4, phantoms) (DECISIONS.md D26).
5. **Repeats.** A train that drops off one poll and comes back (board
   flicker) ends twice. On one board, an arrival followed by another within
   ``match_tolerance_seconds`` is the same train, and only the later one is
   kept: two trains cannot use one platform that close together.

**Time to go.** Every rule above reads the time to go as of the poll,
``expected_arrival - polled_at`` in whole seconds (truncated, floored at 0),
not TfL's ``time_to_station``. That is relative to the prediction's own
timestamp, about 40 s older than the poll, and repeats between polls, so a
train whose last sighting shows 121 s may be 16 s from the platform
(week-one review, Q4; DECISIONS.md D40). ``time_to_station`` is kept on the
output for diagnosis only.

"Next poll" means the station's next *successful* poll. When polls failed in
between, the arrival is still inferred, and ``confirmed_at`` shows how long
after ``last_seen_at`` the absence was seen, so a headway that spans a
collection gap can be dropped downstream rather than read as a long gap.

Arrivals at the end of the input are provisional: a train showing 0 s at
the last poll is an arrival only until a later poll links it onward. An
incremental SQL model must read at least one poll past the end of the window
it writes, and the oracle comparison must give both sides the same input.

All times are UTC. Nothing here knows about London time or schedules.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    # Lines whose vehicle_id is stable per train (D27). Empty by default; the
    # SQL model reads the list from seed_vehicle_id_lines.
    vehicle_id_lines: frozenset[str] = field(default_factory=frozenset)
    # Ids several trains share at once (week-one review, Q2): no id at all.
    placeholder_vehicle_ids: frozenset[str] = frozenset({"000", "001"})


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
    # Whole seconds from the poll to expected_arrival, floored at 0. Set by
    # infer_arrivals from the station's poll time; when None (a prediction
    # built by hand for link_polls), time_to_station stands in.
    seconds_to_arrival: int | None = None

    @property
    def to_go(self) -> int:
        """Seconds to go as the inference reads them."""
        if self.seconds_to_arrival is not None:
            return self.seconds_to_arrival
        return self.time_to_station

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
    # Seconds to go at the last sighting, as the arrival rule read them.
    last_seconds_to_arrival: int
    # TfL's own figure at the last sighting, stale by about 40 s; diagnostic.
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


def seconds_to_arrival(expected_arrival: datetime, polled_at: datetime) -> int:
    """Whole seconds from the poll to the expected arrival, floored at 0.

    Truncated toward zero, as Athena's ``date_diff('second', ...)`` does, so
    the reference and the SQL model agree to the second.
    """
    return max(0, int((expected_arrival - polled_at).total_seconds()))


_MILLISECOND = timedelta(milliseconds=1)


def _ms(delta: timedelta) -> int:
    """A time difference in whole milliseconds.

    Comparisons run on integer milliseconds, as the SQL model's
    ``date_diff('millisecond', ...)`` does, so a value exactly on a tolerance
    falls the same side in both. Poll times carry milliseconds and expected
    arrivals whole seconds, so nothing is lost.
    """
    return delta // _MILLISECOND


def _tolerance_ms(seconds: float) -> int:
    return round(seconds * 1000)


def _vehicle(prediction: Prediction, params: InferenceParams) -> str | None:
    vehicle_id = prediction.vehicle_id
    if vehicle_id is None or vehicle_id in params.placeholder_vehicle_ids:
        return None
    return vehicle_id


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"naive datetime {value!r}; inference works in UTC only")
    return value.astimezone(UTC)


def drop_duplicates(
    predictions: Sequence[Prediction], tolerance_seconds: float
) -> list[Prediction]:
    """One board in one poll, without the second listing of the same train.

    Ordered by (expected_arrival, prediction_id, vehicle_id, time_to_station);
    a prediction within ``tolerance_seconds`` of the one before it is dropped.
    The last two keys only settle which of two rows with one id and one time
    is kept (a DLR board shares one id across trains). Comparing with the
    previous row, not the previous kept row, keeps this a window function in
    SQL (``lag``).
    """
    ordered = sorted(
        predictions,
        key=lambda p: (p.expected_arrival, p.prediction_id, p.vehicle_id or "", p.time_to_station),
    )
    tolerance = _tolerance_ms(tolerance_seconds)
    kept: list[Prediction] = []
    for index, prediction in enumerate(ordered):
        if index > 0:
            gap = _ms(prediction.expected_arrival - ordered[index - 1].expected_arrival)
            if gap <= tolerance:
                continue
        kept.append(prediction)
    return kept


def _distance(
    earlier: Prediction, later: Prediction, poll_gap_ms: int, params: InferenceParams
) -> int | None:
    """How far apart two sightings are as the same train, in milliseconds; None if they cannot be."""
    tolerance = _tolerance_ms(params.match_tolerance_seconds)
    moved = _ms(later.expected_arrival - earlier.expected_arrival)
    earlier_vehicle = _vehicle(earlier, params)
    later_vehicle = _vehicle(later, params)
    if (
        earlier.line_id in params.vehicle_id_lines
        and earlier_vehicle is not None
        and later_vehicle is not None
    ):
        # A shared id still needs a plausible time, so an id reused later in
        # the day cannot chain two trains.
        if earlier_vehicle == later_vehicle and abs(moved) <= poll_gap_ms + tolerance:
            return 0
        return None
    if abs(moved) <= tolerance:
        return abs(moved)
    # Only at the platform. A train held short of it is not linked this way:
    # on real boards the next train often takes over the time to station the
    # previous one showed a poll earlier, and would be merged into it (D26).
    standing = later.to_go == 0 and earlier.to_go * 1000 <= tolerance
    clock_drift = abs(moved - poll_gap_ms)
    if standing and clock_drift <= tolerance:
        return tolerance + clock_drift
    return None


def link_polls(
    earlier: Sequence[Prediction],
    later: Sequence[Prediction],
    poll_gap_seconds: float,
    params: InferenceParams = DEFAULT_PARAMS,
) -> dict[int, int]:
    """Mutual nearest-candidate links between one board's two consecutive polls.

    ``poll_gap_seconds`` is the time between the two polls. Returns {index in
    ``earlier``: index in ``later``}. Ties go to the earlier expected arrival,
    then the lower prediction id, so the result does not depend on input
    order.
    """
    gap_ms = _tolerance_ms(poll_gap_seconds)
    best_later: dict[int, int] = {}
    best_earlier: dict[int, int] = {}
    for i, a in enumerate(earlier):
        options = [
            (d, b.expected_arrival, b.prediction_id, j)
            for j, b in enumerate(later)
            if (d := _distance(a, b, gap_ms, params)) is not None
        ]
        if options:
            best_later[i] = min(options)[3]
    for j, b in enumerate(later):
        options = [
            (d, a.expected_arrival, a.prediction_id, i)
            for i, a in enumerate(earlier)
            if (d := _distance(a, b, gap_ms, params)) is not None
        ]
        if options:
            best_earlier[j] = min(options)[3]
    return {i: j for i, j in best_later.items() if best_earlier.get(j) == i}


def _station_polls(polls: Iterable[BoardPoll]) -> dict[str, list[BoardPoll]]:
    by_station: dict[str, dict[str, BoardPoll]] = defaultdict(dict)
    for poll in polls:
        normalised = BoardPoll(poll.poll_id, poll.request_path, _utc(poll.polled_at))
        known = by_station[poll.request_path].setdefault(poll.poll_id, normalised)
        if known.polled_at != normalised.polled_at:
            raise ValueError(f"poll {poll.poll_id} at {poll.request_path} has two poll times")
    return {
        station: sorted(found.values(), key=lambda p: (p.polled_at, p.poll_id))
        for station, found in by_station.items()
    }


def _trains_on_board(
    board_polls: list[list[Prediction]], station_polls: list[BoardPoll], params: InferenceParams
) -> list[_Train]:
    """Chain one board's observations, poll by poll, into trains."""
    trains: list[_Train] = []
    previous: list[tuple[Prediction, _Train]] = []
    for poll_index, board in enumerate(board_polls):
        current = drop_duplicates(board, params.duplicate_tolerance_seconds)
        gap = (
            (station_polls[poll_index].polled_at - station_polls[poll_index - 1].polled_at)
            if poll_index > 0
            else None
        )
        links = (
            link_polls([p for p, _ in previous], current, gap.total_seconds(), params)
            if gap is not None
            else {}
        )
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
        if prediction.to_go > params.arrival_threshold_seconds:
            return None
        rule: ArrivalRule = "at_platform" if prediction.to_go == 0 else "left_board"
        confirmed_at = station_polls[next_index].polled_at
    else:
        if prediction.to_go != 0:
            return None
        rule = "at_platform"
        confirmed_at = None
    vehicle_ids = [
        v for o in train.observations if (v := _vehicle(o.prediction, params)) is not None
    ]
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
        last_seconds_to_arrival=prediction.to_go,
        last_time_to_station=prediction.time_to_station,
        vehicle_id=vehicle_ids[-1] if vehicle_ids else None,
        first_seen_at=station_polls[train.observations[0].poll_index].polled_at,
        last_seen_at=station_polls[last.poll_index].polled_at,
        confirmed_at=confirmed_at,
        observations=len(train.observations),
    )


def collapse_repeats(
    arrivals: Sequence[InferredArrival], tolerance_seconds: float
) -> list[InferredArrival]:
    """One board's arrivals without the earlier of two within the tolerance.

    Ordered by (arrival_time, last_poll time, last_prediction_id); an arrival
    followed within ``tolerance_seconds`` by the next is dropped, so the
    later sighting of a train that flickered off the board is kept. In SQL a
    ``lead`` over the same order.
    """
    ordered = sorted(arrivals, key=lambda a: (a.arrival_time, a.last_seen_at, a.last_prediction_id))
    kept: list[InferredArrival] = []
    for index, arrival in enumerate(ordered):
        if index + 1 < len(ordered):
            gap = _ms(ordered[index + 1].arrival_time - arrival.arrival_time)
            if gap <= _tolerance_ms(tolerance_seconds):
                continue
        kept.append(arrival)
    return kept


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
        expected_arrival = _utc(prediction.expected_arrival)
        polled_at = station_polls[prediction.request_path][poll_index[key]].polled_at
        normalised = replace(
            prediction,
            expected_arrival=expected_arrival,
            seconds_to_arrival=seconds_to_arrival(expected_arrival, polled_at),
        )
        boards[prediction.board][poll_index[key]].append(normalised)

    arrivals: list[InferredArrival] = []
    for board in sorted(boards, key=_board_sort_key):
        polls_here = station_polls[board[0]]
        per_poll = [boards[board].get(index, []) for index in range(len(polls_here))]
        found = [
            arrival
            for train in _trains_on_board(per_poll, polls_here, params)
            if (arrival := _arrival(train, polls_here, params)) is not None
        ]
        arrivals.extend(collapse_repeats(found, params.match_tolerance_seconds))
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
    if isinstance(value, Decimal):
        return int(value)
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
    "last_seconds_to_arrival",
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
