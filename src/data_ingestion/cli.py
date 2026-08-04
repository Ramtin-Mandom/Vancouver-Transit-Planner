"""Command-line entry point for the transit data loader."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_DATA_DIR, ConfigurationError, DatabaseConfig
from .loader import TABLES, LoadSummary, TransitLoader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load extracted GTFS data into PostgreSQL."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate without a database connection"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="explicitly truncate and replace the required transit tables",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="directory containing extracted text files",
    )
    return parser


def print_summary(summary: LoadSummary) -> None:
    """Print per-table counts and a final status summary."""
    for table, count in summary.successful.items():
        print(f"{table}: {count:,} rows")
    print(
        "\nSummary: "
        f"{len(summary.successful)} successful, "
        f"{len(summary.skipped)} skipped, "
        f"{len(summary.failed)} failed"
    )


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.dry_run and args.replace:
        print("error: --dry-run and --replace cannot be combined", file=sys.stderr)
        return 2
    try:
        config = None if args.dry_run else DatabaseConfig.from_environment()
        loader = TransitLoader(args.data_dir.resolve(), config)
        summary = (
            loader.dry_run() if args.dry_run else loader.load(replace=args.replace)
        )
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        print("Successful tables: none", file=sys.stderr)
        print(
            "Skipped tables: " + ", ".join(spec.table for spec in TABLES),
            file=sys.stderr,
        )
        print("Failed operation: validation/import transaction", file=sys.stderr)
        print(
            f"Summary: 0 successful, {len(TABLES)} skipped, 1 failed",
            file=sys.stderr,
        )
        return 1
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
