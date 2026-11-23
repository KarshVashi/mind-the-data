"""The demand download keeps to its pins and out of the repository (ADR-020)."""

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from mind_the_data.demand.numbat import DemandFileError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fetch_demand_data.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_demand_data", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def client_serving(body: bytes) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )


def test_fetch_keeps_a_file_whose_hash_matches(tmp_path: Path) -> None:
    script = load_script()
    body = b"workbook bytes"
    pinned = script.Pinned("x", "NUMBAT/NUMBAT 2025/x.xlsx", hashlib.sha256(body).hexdigest())
    assert pinned.url == "https://crowding.data.tfl.gov.uk/NUMBAT/NUMBAT%202025/x.xlsx"
    with client_serving(body) as client:
        path = script.fetch(pinned, tmp_path, client)
    assert path.read_bytes() == body


def test_fetch_refuses_a_replaced_file(tmp_path: Path) -> None:
    script = load_script()
    pinned = script.Pinned("x", "x.xlsx", hashlib.sha256(b"old").hexdigest())
    with client_serving(b"new") as client, pytest.raises(DemandFileError, match="move the pin"):
        script.fetch(pinned, tmp_path, client)
    assert list(tmp_path.iterdir()) == []


def test_raw_files_never_land_in_the_repository() -> None:
    script = load_script()
    with pytest.raises(SystemExit):
        script.main(["--cache-dir", str(ROOT / "transform" / "seeds")])


def test_every_pin_is_a_sha256() -> None:
    script = load_script()
    for pinned in [*script.NUMBAT.values(), script.ANNUAL_COUNTS]:
        assert len(pinned.sha256) == 64
        int(pinned.sha256, 16)
