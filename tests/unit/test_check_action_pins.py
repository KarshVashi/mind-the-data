import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_action_pins.py"
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_action_pins", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "line",
    [
        f"      - uses: actions/checkout@{SHA} # v7.0.1",
        f"        uses: actions/checkout@{SHA}  # v7",
        f'      - uses: "actions/checkout@{SHA}" # 7.0.1',
        f"    uses: org/repo/.github/workflows/build.yml@{SHA} # v1.2.3",
        f"      - uses: github/codeql-action/init@{SHA} # v3.29.0",
        "      - uses: ./.github/actions/local",
        "      - uses: docker://alpine@sha256:" + "a" * 64,
        "      - run: echo uses: actions/checkout@v4",
        "      name: not a uses line",
    ],
)
def test_accepts_pinned_and_unrelated_lines(line: str) -> None:
    assert load_script().check_line(line) is None


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("      - uses: actions/checkout@v4", "40-character"),
        ("      - uses: actions/checkout@main # v4", "40-character"),
        (f"      - uses: actions/checkout@{SHA[:7]} # v7", "40-character"),
        (f"      - uses: actions/checkout@{SHA.upper()} # v7", "40-character"),
        (f"      - uses: actions/checkout@{SHA}", "version comment"),
        (f"      - uses: actions/checkout@{SHA} # pinned", "version comment"),
        (f"      - uses: actions/checkout@{SHA}# v7", "version comment"),
        ("      - uses: docker://alpine:3.20", "digest"),
    ],
)
def test_rejects_unpinned(line: str, reason: str) -> None:
    found = load_script().check_line(line)
    assert found is not None
    assert reason in found[1]


def test_main_reports_file_and_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "good.yml").write_text(f"steps:\n  - uses: actions/checkout@{SHA} # v7.0.1\n")
    (workflows / "bad.yaml").write_text(
        "steps:\n  - run: true\n  - uses: actions/setup-python@v5\n"
    )
    (tmp_path / ".github" / "notes.txt").write_text("uses: not/yaml@v1\n")

    assert load_script().main(["prog", str(tmp_path / ".github")]) == 1
    out = capsys.readouterr().out
    assert f"{workflows / 'bad.yaml'}:3: actions/setup-python@v5" in out
    assert "good.yml" not in out
    assert "notes.txt" not in out


def test_main_passes_when_all_pinned(tmp_path: Path) -> None:
    (tmp_path / "ci.yml").write_text(f"- uses: actions/checkout@{SHA} # v7.0.1\n")
    assert load_script().main(["prog", str(tmp_path)]) == 0


def test_repository_workflows_are_pinned() -> None:
    github = Path(__file__).resolve().parents[2] / ".github"
    assert load_script().main(["prog", str(github)]) == 0
