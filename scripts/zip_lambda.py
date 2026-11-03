"""Zip a Lambda build directory reproducibly.

The same inputs always give a byte-identical zip, so Terraform's
source_code_hash only changes, and the functions only redeploy, when the code
or a pinned dependency changes. Entries are sorted, timestamps fixed and
permissions normalised; bytecode caches are left out because the runtime
writes its own.

Usage: python scripts/zip_lambda.py <build_dir> <zip_path>
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Earliest timestamp the zip format can hold.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
SKIPPED_DIRS = frozenset({"__pycache__"})
SKIPPED_SUFFIXES = frozenset({".pyc", ".pyo"})


def files_to_zip(build_dir: Path) -> list[Path]:
    """Every file under build_dir that belongs in the package, in a stable order."""
    return sorted(
        path
        for path in build_dir.rglob("*")
        if path.is_file()
        and path.suffix not in SKIPPED_SUFFIXES
        and not SKIPPED_DIRS.intersection(path.relative_to(build_dir).parts)
    )


def write_zip(build_dir: Path, zip_path: Path) -> int:
    """Write the zip and return the number of files in it."""
    files = files_to_zip(build_dir)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(build_dir).as_posix(), FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = (0o100000 | mode) << 16
            zf.writestr(info, path.read_bytes(), compresslevel=9)
    return len(files)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    build_dir, zip_path = Path(argv[1]), Path(argv[2])
    if not build_dir.is_dir():
        print(f"zip_lambda: {build_dir} is not a directory", file=sys.stderr)
        return 1
    count = write_zip(build_dir, zip_path)
    print(f"zip_lambda: wrote {zip_path} ({count} files, {zip_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
