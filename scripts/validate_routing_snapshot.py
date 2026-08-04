"""Validate and summarize a routing snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.routing.snapshot import RoutingSnapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="routing snapshot directory")
    args = parser.parse_args()
    snapshot = RoutingSnapshot(args.path)
    try:
        print(json.dumps(snapshot.manifest, indent=2, sort_keys=True))
    finally:
        snapshot.close()


if __name__ == "__main__":
    main()
