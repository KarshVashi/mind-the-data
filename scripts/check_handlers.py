"""Check that every Lambda handler named in Terraform imports from the package.

A schedule or function that points at a handler the package does not contain
deploys cleanly and then fails on every invocation. This happens when the
Terraform change merges before the code it calls, so the check runs on the
built zip, before plan and apply.

The zip is extracted and each handler is imported in a fresh interpreter
(`-I -S`: no site-packages, no PYTHONPATH, not the repository's src/), with
the extracted package as the only source of first-party and pure-Python code.
Compiled wheels in the zip are built for the Lambda platform (arm64 Linux),
so those few are installed again for this machine at the exact versions the
zip holds, checked against the hashes in a `uv export` of uv.lock, and put
ahead of it on sys.path.

Usage: python scripts/check_handlers.py <zip_path> <requirements> <terraform_dir> [...]
  requirements: `uv export` output with hashes (make handlers writes it)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# A literal handler argument in a module call or resource, e.g.
#   handler = "mind_the_data.ingest.poller.handler"
# A handler passed through a variable or local would not be seen; the unit
# test that lists the prod stack's handlers catches that change.
HANDLER_RE = re.compile(r'^\s*handler\s*=\s*"([^"]+)"', re.MULTILINE)
DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")

# Runs in the clean interpreter. argv: package dir, then handlers.
PROBE = """
import importlib, json, sys
pkg = sys.argv[1]
results = {}
for handler in sys.argv[2:]:
    module_name, _, attr = handler.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        results[handler] = f"import {module_name} failed: {type(exc).__name__}: {exc}"
        continue
    origin = getattr(module, "__file__", "") or ""
    if not origin.startswith(pkg):
        results[handler] = f"{module_name} was imported from {origin}, not the package"
    elif not callable(getattr(module, attr, None)):
        results[handler] = f"{module_name} has no callable {attr!r}"
    else:
        results[handler] = None
print(json.dumps(results))
"""


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    pure: bool


def find_handlers(tf_dirs: list[Path]) -> list[str]:
    """Every handler string in the given Terraform directories (not recursive)."""
    found: set[str] = set()
    for tf_dir in tf_dirs:
        for path in sorted(tf_dir.glob("*.tf")):
            found.update(HANDLER_RE.findall(path.read_text()))
    return sorted(found)


def malformed(handlers: list[str]) -> list[str]:
    """Handlers that are not a dotted module path followed by an attribute."""
    return [h for h in handlers if not DOTTED_RE.match(h)]


def parse_wheel(metadata: str, wheel: str) -> Wheel:
    """Name, version and purity from a dist-info's METADATA and WHEEL files."""
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        if not line.strip():
            break  # headers end at the first blank line
        key, sep, value = line.partition(":")
        if sep and key in ("Name", "Version") and key not in fields:
            fields[key] = value.strip()
    tags = [
        line.partition(":")[2].strip() for line in wheel.splitlines() if line.startswith("Tag:")
    ]
    pure = bool(tags) and all(tag.endswith("-none-any") for tag in tags)
    return Wheel(name=fields["Name"], version=fields["Version"], pure=pure)


def wheels_in(package_dir: Path) -> list[Wheel]:
    wheels = []
    for dist_info in sorted(package_dir.glob("*.dist-info")):
        metadata, wheel = dist_info / "METADATA", dist_info / "WHEEL"
        if metadata.is_file() and wheel.is_file():
            wheels.append(parse_wheel(metadata.read_text(), wheel.read_text()))
    return wheels


def normalise(name: str) -> str:
    """PEP 503 name, so pydantic_core matches pydantic-core."""
    return re.sub(r"[-_.]+", "-", name).lower()


def hashed_pins(requirements: str, wheels: list[Wheel]) -> str:
    """The requirement entries, hashes included, for exactly these wheels.

    Each entry must pin the same version the zip holds, so the host build is
    the locked release and nothing outside uv.lock is installed.
    """
    wanted = {normalise(w.name): w.version for w in wheels}
    entries: dict[str, str] = {}
    current: list[str] = []
    for line in requirements.splitlines() + [""]:
        if line and line[0].isspace():
            current.append(line)
            continue
        if current:
            head = current[0].split(";")[0].strip().rstrip("\\").strip()
            name, sep, version = head.partition("==")
            if sep and wanted.get(normalise(name)) == version.strip():
                entries[normalise(name)] = "\n".join(current)
        current = [line] if line and not line.startswith("#") else []
    missing = sorted(set(wanted) - set(entries))
    if missing:
        raise RuntimeError(f"no hashed pin at the zip's version for: {', '.join(missing)}")
    return "\n".join(entries[name] for name in sorted(entries)) + "\n"


def install_host_wheels(wheels: list[Wheel], requirements: str, target: Path) -> None:
    """Install the compiled wheels for this machine, at the zip's locked versions."""
    compiled = [w for w in wheels if not w.pure]
    target.mkdir(parents=True, exist_ok=True)
    if not compiled:
        return
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is needed to install host builds of compiled wheels")
    pins = target.parent / "host-requirements.txt"
    pins.write_text(hashed_pins(requirements, compiled))
    subprocess.run(
        [uv, "pip", "install", "--quiet", "--no-deps", "--only-binary", ":all:", "--require-hashes"]
        + ["--python", sys.executable, "--target", str(target), "-r", str(pins)],
        check=True,
    )


def probe(package_dir: Path, host_dir: Path, handlers: list[str]) -> dict[str, str | None]:
    """Import each handler in a clean interpreter; map handler to error or None."""
    bootstrap = (
        f"import sys; sys.path[:0] = [{str(host_dir)!r}, {str(package_dir)!r}]; "
        f"sys.argv = ['probe', {str(package_dir)!r}] + sys.argv[1:]; exec({PROBE!r})"
    )
    # No AWS credentials or project settings: an import must not need them.
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", bootstrap, *handlers],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"probe interpreter failed:\n{result.stderr}")
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"probe interpreter printed nothing:\n{result.stderr}")
    parsed: dict[str, str | None] = json.loads(lines[-1])
    return parsed


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    zip_path, requirements = Path(argv[1]), Path(argv[2])
    tf_dirs = [Path(d) for d in argv[3:]]
    if not zip_path.is_file():
        print(f"check_handlers: {zip_path} not found (run make package)", file=sys.stderr)
        return 1

    handlers = find_handlers(tf_dirs)
    if not handlers:
        print(f"check_handlers: no handler found in {', '.join(map(str, tf_dirs))}")
        return 1
    bad = malformed(handlers)
    if bad:
        print(f"check_handlers: not module.attribute: {', '.join(bad)}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="check_handlers_") as tmp:
        package_dir, host_dir = Path(tmp) / "package", Path(tmp) / "host"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(package_dir)
        install_host_wheels(wheels_in(package_dir), requirements.read_text(), host_dir)
        results = probe(package_dir.resolve(), host_dir.resolve(), handlers)

    failed = 0
    for handler in handlers:
        error = results.get(handler, "not checked")
        if error is None:
            print(f"ok    {handler}")
        else:
            failed += 1
            print(f"FAIL  {handler}: {error}")
    if failed:
        print(f"check_handlers: {failed} of {len(handlers)} handlers missing from {zip_path}")
        return 1
    print(f"check_handlers: all {len(handlers)} handlers import from {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
