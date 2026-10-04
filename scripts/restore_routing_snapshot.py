"""Restore the repository's offline routing artifact without PostgreSQL."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

from src.routing.snapshot import RoutingSnapshot

DEFAULT_ARCHIVE = Path(__file__).resolve().parents[1] / "data/routing_snapshot.zip"


def restore(archive: Path, output: Path) -> None:
    if output.exists():
        raise ValueError(f"Snapshot destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / "snapshot"
        staged.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise ValueError("Snapshot archive has duplicate files or no manifest")
            for name in names:
                if (
                    "/" in name
                    or "\\" in name
                    or ":" in name
                    or not (name == "manifest.json" or name.endswith(".npy"))
                ):
                    raise ValueError(
                        "Snapshot archive must contain only flat snapshot files"
                    )
                with bundle.open(name) as source, (staged / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        snapshot = RoutingSnapshot(staged)
        snapshot.close()
        staged.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=Path("data/routing_snapshot"))
    args = parser.parse_args()
    restore(args.archive, args.output)
    print("Repository routing snapshot restored and validated; no database required.")


if __name__ == "__main__":
    main()
