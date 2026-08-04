"""Download and safely extract the current TransLink static GTFS feed."""

from __future__ import annotations

import argparse
import shutil
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_URL = "https://gtfs-static.translink.ca/gtfs/google_transit.zip"


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"unsafe archive member: {member.filename}")
        bundle.extractall(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="GTFS ZIP download URL")
    parser.add_argument(
        "--archive", type=Path, default=Path("data/raw/google_transit.zip")
    )
    parser.add_argument("--extract-dir", type=Path, default=Path("data/extracted"))
    parser.add_argument("--force", action="store_true", help="replace existing output")
    args = parser.parse_args()

    extracted_files = (
        [path for path in args.extract_dir.iterdir() if path.name != ".gitkeep"]
        if args.extract_dir.is_dir()
        else []
    )
    if not args.force and (args.archive.exists() or extracted_files):
        raise SystemExit("output exists; pass --force to replace generated GTFS data")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    args.extract_dir.mkdir(parents=True, exist_ok=True)
    with (
        urllib.request.urlopen(args.url, timeout=120) as response,
        args.archive.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)
    safe_extract(args.archive, args.extract_dir)
    print(f"downloaded {args.archive} and extracted to {args.extract_dir}")


if __name__ == "__main__":
    main()
