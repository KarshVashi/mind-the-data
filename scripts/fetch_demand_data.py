"""Download TfL's typical-demand files and rebuild the demand seeds (ADR-020).

    uv run python scripts/fetch_demand_data.py                 # download, verify, write seeds
    uv run python scripts/fetch_demand_data.py --check         # fail if the seeds are stale
    uv run python scripts/fetch_demand_data.py --cache-dir /tmp/demand

The raw workbooks (about 61 MB for NUMBAT 2025's five day types) are kept in
the cache directory, never in the repository. Each is pinned by URL and
sha256: a file TfL replaces in place fails the hash check, and the pin is
then moved deliberately (runbook, "Demand data"). Only the small derived
seeds are committed:

- transform/seeds/seed_line_demand.csv: boarders and summed link loads per
  NUMBAT line, service day type and London hour;
- transform/seeds/seed_link_demand.csv: each link's typical daily load;
- transform/seeds/seed_station_demand.csv: typical entries plus exits.

The source is "Powered by TfL Open Data" under TfL's transport data licence
(based on OGL v2.0), which allows copying, adapting and redistributing.
"""

import argparse
import csv
import hashlib
import io
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from mind_the_data.demand.numbat import (
    DayTypeInput,
    DemandFileError,
    line_demand_rows,
    link_demand_rows,
    parse_line_boarders,
    parse_link_loads,
    parse_station_counts,
    quarter_hour_columns,
    station_demand_rows,
)
from mind_the_data.demand.xlsx import read_sheet

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "transform" / "seeds"
STORE = "https://crowding.data.tfl.gov.uk"


@dataclass(frozen=True)
class Pinned:
    name: str
    path: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{STORE}/{self.path.replace(' ', '%20')}"


# NUMBAT 2025 (typical autumn 2025, published 2026-08-10) and the 2025
# annual counts (published 2026-08-14). Refresh once a year, after TfL
# publishes the next autumn's files (ADR-020).
NUMBAT = {
    day: Pinned(f"NBT25{day}", f"NUMBAT/NUMBAT 2025/NBT25{day}_Outputs.xlsx", digest)
    for day, digest in {
        "MON": "89ebca13a5f611e255c3cc31eb34917a7262c642135e943ee6714df17a108b05",
        "TWT": "a10e5f767750538ecac99cf0d2131243672574df88b9c933f03b9357c5f67db2",
        "FRI": "22eeb8fe2fd5ee2c974aaff81c7f3e114c53e39f46cd03c273c072974f5d9b5c",
        "SAT": "64cdfa78f312332740f9a562f81f5dac12114bb439d4a219bcf095ee28ad3821",
        "SUN": "c064331e50cf92edd022ef9f1f7efb9c21ca38edabb963a7bf5758682d60bc8c",
    }.items()
}
ANNUAL_COUNTS = Pinned(
    "AC2025",
    "Annual Station Counts/2025/AC2025_AnnualisedEntryExit_public.xlsx",
    "041dcf60d6f2afb07e526e34bebfffb811839a609fc07286a2a5aa1c548b1fdb",
)
ANNUAL_COUNTS_SHEET = "AC25_v08.2_E9_annualised"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(pinned: Pinned, cache_dir: Path, client: httpx.Client) -> Path:
    """Download `pinned` into the cache unless a verified copy is there."""
    target = cache_dir / Path(pinned.path).name
    if target.exists() and sha256_of(target) == pinned.sha256:
        return target
    partial = target.with_suffix(target.suffix + ".part")
    with client.stream("GET", pinned.url) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    actual = sha256_of(partial)
    if actual != pinned.sha256:
        partial.unlink()
        raise DemandFileError(
            f"{pinned.url} has sha256 {actual}, pinned {pinned.sha256}. TfL has "
            "replaced the file: review it and move the pin (runbook, Demand data)."
        )
    partial.replace(target)
    return target


def numbat_input(path: Path) -> DayTypeInput:
    link_rows = list(read_sheet(path, "Link_Loads"))
    boarder_rows = list(read_sheet(path, "Line_Boarders"))
    link_header = next(row for row in link_rows if row and row[0] == "Link")
    boarder_header = next(row for row in boarder_rows if row and row[0] == "Mode")
    hours = [hour for _, hour in quarter_hour_columns(link_header)]
    if hours != [hour for _, hour in quarter_hour_columns(boarder_header)]:
        raise DemandFileError(f"{path.name}: sheets disagree on quarter-hour order")
    return DayTypeInput(
        links=parse_link_loads(link_rows),
        boarders=parse_line_boarders(boarder_rows),
        hours=hours,
    )


def to_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build(cache_dir: Path) -> dict[str, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        inputs = {
            day: numbat_input(fetch(pinned, cache_dir, client)) for day, pinned in NUMBAT.items()
        }
        counts_path = fetch(ANNUAL_COUNTS, cache_dir, client)
    counts = parse_station_counts(list(read_sheet(counts_path, ANNUAL_COUNTS_SHEET)))
    return {
        "seed_line_demand.csv": to_csv(line_demand_rows(inputs)),
        "seed_link_demand.csv": to_csv(link_demand_rows(inputs)),
        "seed_station_demand.csv": to_csv(station_demand_rows(counts)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "mind-the-data" / "demand",
        help="where the raw workbooks are kept (never inside the repository)",
    )
    parser.add_argument("--check", action="store_true", help="fail if a seed would change")
    args = parser.parse_args(argv)
    if args.cache_dir.resolve().is_relative_to(ROOT):
        parser.error("keep the raw workbooks outside the repository")

    seeds = build(args.cache_dir)
    stale = []
    for name, content in seeds.items():
        path = SEEDS / name
        current = path.read_text() if path.exists() else None
        if current == content:
            print(f"{name}: unchanged ({content.count(chr(10)) - 1} rows)")
            continue
        stale.append(name)
        if not args.check:
            path.write_text(content)
            print(f"{name}: written ({content.count(chr(10)) - 1} rows)")
    if args.check and stale:
        print(f"stale: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
