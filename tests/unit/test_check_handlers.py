import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_handlers.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_handlers", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_finds_handlers_in_module_inputs(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'module "a" {\n  handler                  = "pkg.a.handler"\n}\n'
        'module "b" {\n  handler = "pkg.b.handler"\n  # handler = "pkg.commented.out"\n}\n'
    )
    (tmp_path / "other.tf").write_text('module "c" {\n  handler = "pkg.a.handler"\n}\n')
    (tmp_path / "notes.md").write_text('handler = "pkg.ignored.handler"\n')
    assert load_script().find_handlers([tmp_path]) == ["pkg.a.handler", "pkg.b.handler"]


def test_prod_stack_names_both_collector_handlers() -> None:
    handlers = load_script().find_handlers([ROOT / "infra" / "envs" / "prod"])
    assert handlers == [
        "mind_the_data.ingest.poller.handler",
        "mind_the_data.ingest.reference.handler",
    ]


@pytest.mark.parametrize(
    ("handler", "ok"),
    [
        ("pkg.mod.handler", True),
        ("mod.handler", True),
        ("handler", False),
        ("pkg/mod.handler", False),
        ("pkg.mod.", False),
        ("pkg.mod.handler()", False),
    ],
)
def test_malformed(handler: str, ok: bool) -> None:
    assert (load_script().malformed([handler]) == []) is ok


def test_parse_wheel_purity() -> None:
    cs = load_script()
    metadata = "Metadata-Version: 2.1\nName: pydantic_core\nVersion: 2.50.1\n\nName: body\n"
    compiled = "Wheel-Version: 1.0\nTag: cp312-cp312-manylinux_2_17_aarch64\n"
    pure = "Wheel-Version: 1.0\nTag: py2-none-any\nTag: py3-none-any\n"
    assert cs.parse_wheel(metadata, compiled) == cs.Wheel("pydantic_core", "2.50.1", pure=False)
    assert cs.parse_wheel(metadata, pure).pure is True


def make_zip(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "lambda.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def test_main_passes_and_fails_on_the_zip_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cs = load_script()
    tf = tmp_path / "tf"
    tf.mkdir()
    (tf / "main.tf").write_text(
        'module "a" {\n  handler = "fakepkg.poller.handler"\n}\n'
        'module "b" {\n  handler = "fakepkg.reference.handler"\n}\n'
    )
    # Only the poller is in the package: the reference module is the code
    # that has not merged yet.
    zip_path = make_zip(
        tmp_path,
        {
            "fakepkg/__init__.py": "",
            "fakepkg/poller.py": "def handler(event, context):\n    return {}\n",
        },
    )
    assert cs.main(["prog", str(zip_path), str(tf)]) == 1
    out = capsys.readouterr().out
    assert "ok    fakepkg.poller.handler" in out
    assert "FAIL  fakepkg.reference.handler" in out

    zip_path.unlink()
    zip_path = make_zip(
        tmp_path,
        {
            "fakepkg/__init__.py": "",
            "fakepkg/poller.py": "def handler(event, context):\n    return {}\n",
            "fakepkg/reference.py": "handler = None\n",
        },
    )
    assert cs.main(["prog", str(zip_path), str(tf)]) == 1
    assert "has no callable 'handler'" in capsys.readouterr().out


def test_main_ignores_the_repository_source(tmp_path: Path) -> None:
    # mind_the_data is importable in the test environment, but not from this
    # zip, so the check must fail.
    tf = tmp_path / "tf"
    tf.mkdir()
    (tf / "main.tf").write_text(
        'module "a" {\n  handler = "mind_the_data.ingest.poller.handler"\n}\n'
    )
    zip_path = make_zip(tmp_path, {"other/__init__.py": ""})
    assert load_script().main(["prog", str(zip_path), str(tf)]) == 1


def test_main_needs_handlers(tmp_path: Path) -> None:
    zip_path = make_zip(tmp_path, {"x.py": ""})
    assert load_script().main(["prog", str(zip_path), str(tmp_path)]) == 1
