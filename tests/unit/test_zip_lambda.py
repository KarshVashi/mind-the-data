import importlib.util
import os
import zipfile
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "zip_lambda.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zip_lambda", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_build_dir(root: Path) -> Path:
    build = root / "build"
    (build / "mind_the_data" / "__pycache__").mkdir(parents=True)
    (build / "mind_the_data" / "__init__.py").write_text("")
    (build / "mind_the_data" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (build / "config").mkdir()
    (build / "config" / "lines.yaml").write_text("lines: []\n")
    (build / "config" / ".gitkeep").write_text("")
    (build / "bin").mkdir()
    (build / "bin" / "httpx").write_text("#!/usr/local/bin/python3\n")
    (build / "pkg").mkdir()
    (build / "pkg" / "tool").write_text("#!/bin/sh\n")
    (build / "pkg" / "tool").chmod(0o755)
    return build


def test_zip_is_byte_identical_across_builds(tmp_path: Path) -> None:
    zl = load_script()
    build = make_build_dir(tmp_path)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    zl.write_zip(build, first)
    # A later mtime must not change the output.
    os.utime(build / "config" / "lines.yaml", (2_000_000_000, 2_000_000_000))
    zl.write_zip(build, second)

    assert first.read_bytes() == second.read_bytes()


def test_zip_skips_bytecode_placeholders_and_scripts(tmp_path: Path) -> None:
    zl = load_script()
    build = make_build_dir(tmp_path)
    out = tmp_path / "dist" / "lambda.zip"

    count = zl.write_zip(build, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        modes = {i.filename: (i.external_attr >> 16) & 0o777 for i in zf.infolist()}
    assert names == ["config/lines.yaml", "mind_the_data/__init__.py", "pkg/tool"]
    assert count == 3
    assert modes["pkg/tool"] == 0o755
    assert modes["config/lines.yaml"] == 0o644
