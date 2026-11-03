"""config/lines.yaml and config/stations.yaml: the real files and the rules."""

from pathlib import Path

import pytest

from mind_the_data.common.config import (
    MAX_DISTINCT_STATIONS,
    ConfigError,
    Line,
    Mode,
    Station,
    StationRole,
    default_config_dir,
    distinct_station_ids,
    load_config,
    load_lines,
    load_stations,
    validate_stations,
)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
# Line status and line disruption are one call each per poll (PLAN 5.1);
# budget from 5.3. Stop disruption runs every fifth minute and is the headroom.
FIXED_CALLS_PER_MINUTE = 2
CALL_BUDGET_PER_MINUTE = 52
MODES_IN_SCOPE = {"tube", "elizabeth-line", "dlr", "overground", "tram"}

# Plausible station-level ids for the small test files.
TUBE_A, TUBE_B, TUBE_C = "940GZZLUAAA", "940GZZLUBBB", "940GZZLUCCC"
RAIL_A = "910GAAAAAA"


@pytest.fixture(scope="module")
def real_config() -> tuple[list[Line], list[Station]]:
    return load_config(REPO_CONFIG)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


# alpha is a tube line (940GZZ ids); beta is an Overground line (910G ids).
LINES_YAML = """
lines:
  - {id: alpha, name: Alpha, mode: tube, colour: "#000000", low_volume: false}
  - {id: beta, name: Beta, mode: overground, colour: "#FFFFFF", low_volume: true, min_stations: 1}
"""


def _station(naptan_id: str, name: str = "A") -> str:
    return f"""
    - naptan_id: {naptan_id}
      name: {name}
      role: core
      reason: test"""


def _stations_yaml(alpha_ids: list[str], beta_ids: list[str]) -> str:
    """A stations file for lines alpha and beta; a line with no ids is left out."""
    text = "lines:\n"
    for line_id, ids in (("alpha", alpha_ids), ("beta", beta_ids)):
        if ids:
            text += f"  {line_id}:" + "".join(_station(i, f"S{i}") for i in ids) + "\n"
    return text


# ---- the real files -----------------------------------------------------------


def test_real_config_loads_and_validates(real_config: tuple[list[Line], list[Station]]) -> None:
    lines, stations = real_config
    assert len(lines) == 20
    assert len(stations) > len(lines)
    assert {line.mode for line in lines} == MODES_IN_SCOPE


def test_every_line_has_stations(real_config: tuple[list[Line], list[Station]]) -> None:
    lines, stations = real_config
    assert {line.id for line in lines} == {s.line_id for s in stations}


def test_distinct_stations_fit_the_call_budget(
    real_config: tuple[list[Line], list[Station]],
) -> None:
    _, stations = real_config
    distinct = distinct_station_ids(stations)
    assert len(distinct) <= MAX_DISTINCT_STATIONS == 50
    assert len(distinct) + FIXED_CALLS_PER_MINUTE <= CALL_BUDGET_PER_MINUTE


def test_low_volume_lines_match_the_plan(real_config: tuple[list[Line], list[Station]]) -> None:
    lines, _ = real_config
    assert {line.id for line in lines if line.low_volume} == {"tram", "liberty"}


def test_division_seeds_not_set_before_m6(real_config: tuple[list[Line], list[Station]]) -> None:
    lines, _ = real_config
    assert all(line.division_seed is None for line in lines)


def test_branching_lines_measure_a_branch_end(
    real_config: tuple[list[Line], list[Station]],
) -> None:
    _, stations = real_config
    branching = {
        "northern",
        "central",
        "district",
        "metropolitan",
        "piccadilly",
        "elizabeth",
        "dlr",
    }
    for line_id in branching:
        roles = {s.role for s in stations if s.line_id == line_id}
        assert roles - {"core"}, f"{line_id} has no branch or terminus station"


def test_default_config_dir_is_repo_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    assert default_config_dir() == REPO_CONFIG


def test_default_config_dir_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    assert default_config_dir() == tmp_path


# ---- rules, on small files ------------------------------------------------------


def test_distinct_station_ids_sorted_and_unique(tmp_path: Path) -> None:
    path = _write(tmp_path, "s.yaml", _stations_yaml([TUBE_B, TUBE_A], [RAIL_A]))
    assert distinct_station_ids(load_stations(path)) == [RAIL_A, TUBE_A, TUBE_B]


def test_valid_small_config(tmp_path: Path) -> None:
    _write(tmp_path, "lines.yaml", LINES_YAML)
    _write(tmp_path, "stations.yaml", _stations_yaml([TUBE_A, TUBE_B, TUBE_C], [RAIL_A]))
    lines, stations = load_config(tmp_path)
    assert [line.id for line in lines] == ["alpha", "beta"]
    assert len(stations) == 4


def test_unknown_line_rejected(tmp_path: Path) -> None:
    lines = load_lines(_write(tmp_path, "lines.yaml", LINES_YAML))
    text = (
        _stations_yaml([TUBE_A, TUBE_B, TUBE_C], [RAIL_A])
        + f"  gamma:{_station(TUBE_A, f'S{TUBE_A}')}\n"
    )
    stations = load_stations(_write(tmp_path, "s.yaml", text))
    with pytest.raises(ConfigError, match="gamma"):
        validate_stations(lines, stations)


@pytest.mark.parametrize(
    "bad_id",
    [
        "HUBOXC",  # hub, not a station
        "940gzzluoxc",  # lowercase
        "940GZZLU",  # too short
        "940GZZ-LUOXC",
        "490G000462",  # bus stop cluster
        "910",
        "",
    ],
)
def test_bad_naptan_id_rejected(tmp_path: Path, bad_id: str) -> None:
    path = _write(tmp_path, "s.yaml", _stations_yaml([f'"{bad_id}"'], []))
    with pytest.raises(ConfigError, match="naptan_id"):
        load_stations(path)


@pytest.mark.parametrize(("alpha", "beta"), [([RAIL_A], [RAIL_A]), ([TUBE_A], [TUBE_A])])
def test_naptan_prefix_must_match_mode(tmp_path: Path, alpha: list[str], beta: list[str]) -> None:
    lines = load_lines(_write(tmp_path, "lines.yaml", LINES_YAML))
    stations = load_stations(_write(tmp_path, "s.yaml", _stations_yaml(alpha, beta)))
    with pytest.raises(ConfigError, match="prefix"):
        validate_stations(lines, stations)


def test_duplicate_line_station_pair_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "s.yaml", _stations_yaml([TUBE_A, TUBE_A], []))
    with pytest.raises(ConfigError, match="duplicate"):
        load_stations(path)


def test_same_station_on_two_lines_allowed(tmp_path: Path) -> None:
    text = f"lines:\n  alpha:{_station(TUBE_A)}\n  gamma:{_station(TUBE_A)}\n"
    assert len(load_stations(_write(tmp_path, "s.yaml", text))) == 2


def test_one_id_with_two_names_rejected(tmp_path: Path) -> None:
    text = f"lines:\n  alpha:{_station(TUBE_A, 'One')}\n  gamma:{_station(TUBE_A, 'Two')}\n"
    with pytest.raises(ConfigError, match="more than one name"):
        load_stations(_write(tmp_path, "s.yaml", text))


def test_duplicate_line_id_rejected(tmp_path: Path) -> None:
    text = (
        LINES_YAML + '  - {id: alpha, name: A, mode: dlr, colour: "#123456", low_volume: false}\n'
    )
    with pytest.raises(ConfigError, match="duplicate line ids"):
        load_lines(_write(tmp_path, "lines.yaml", text))


@pytest.mark.parametrize("count", [2, 6])
def test_station_count_outside_range_rejected(tmp_path: Path, count: int) -> None:
    lines = load_lines(_write(tmp_path, "lines.yaml", LINES_YAML))
    ids = [f"940GZZLUS{i:02d}" for i in range(count)]
    stations = load_stations(_write(tmp_path, "s.yaml", _stations_yaml(ids, [RAIL_A])))
    with pytest.raises(ConfigError, match="alpha"):
        validate_stations(lines, stations)


def test_line_without_stations_rejected(tmp_path: Path) -> None:
    lines = load_lines(_write(tmp_path, "lines.yaml", LINES_YAML))
    stations = load_stations(
        _write(tmp_path, "s.yaml", _stations_yaml([TUBE_A, TUBE_B, TUBE_C], []))
    )
    with pytest.raises(ConfigError, match="beta: 0"):
        validate_stations(lines, stations)


def test_more_than_fifty_distinct_stations_rejected() -> None:
    # 11 lines x 5 stations, every id different: valid per line, over budget overall.
    lines = [
        Line(id=f"line-{chr(97 + n)}", name="L", mode=Mode.TUBE, colour="#000000", low_volume=False)
        for n in range(11)
    ]
    stations = [
        Station(
            line_id=line.id,
            naptan_id=f"940GZZLU{n:02d}{i}X",
            name=f"S{n}{i}",
            role=StationRole.CORE,
            reason="test",
        )
        for n, line in enumerate(lines)
        for i in range(5)
    ]
    assert len(distinct_station_ids(stations)) == 55
    with pytest.raises(ConfigError, match="budget"):
        validate_stations(lines, stations)


def test_unknown_station_field_rejected(tmp_path: Path) -> None:
    text = _stations_yaml([TUBE_A], []) + '      platform: "1"\n'
    with pytest.raises(ConfigError, match="platform"):
        load_stations(_write(tmp_path, "s.yaml", text))


def test_non_mapping_station_entry_rejected(tmp_path: Path) -> None:
    text = f"lines:\n  alpha:\n    - {TUBE_A}\n"
    with pytest.raises(ConfigError, match="list of mappings"):
        load_stations(_write(tmp_path, "s.yaml", text))


@pytest.mark.parametrize(
    ("original", "bad"),
    [
        ('colour: "#000000"', 'colour: "#00782a"'),
        ("mode: tube", "mode: bus"),
        ("id: alpha", "id: Alpha"),
    ],
)
def test_bad_line_values_rejected(tmp_path: Path, original: str, bad: str) -> None:
    with pytest.raises(ConfigError):
        load_lines(_write(tmp_path, "lines.yaml", LINES_YAML.replace(original, bad, 1)))


def test_bad_role_rejected(tmp_path: Path) -> None:
    text = _stations_yaml([TUBE_A], []).replace("role: core", "role: hub")
    with pytest.raises(ConfigError, match="role"):
        load_stations(_write(tmp_path, "s.yaml", text))


def test_missing_top_level_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="top-level"):
        load_lines(_write(tmp_path, "lines.yaml", "stations: []\n"))
