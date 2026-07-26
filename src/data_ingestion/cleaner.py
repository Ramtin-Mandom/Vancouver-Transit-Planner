"""Clean structurally incomplete and referentially orphaned extracted rows.

Blank optional GTFS values are preserved. A row is removed only when it has a
malformed width, an empty schema-required value, or a foreign key whose parent
row is not present in the extracted feed.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_DATA_DIR
from .loader import TABLES, TableSpec
from .parsers import DataValidationError, validate_header

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForeignKeyRule:
    """A source column that must reference a retained parent identifier."""

    column: str
    parent: str
    optional: bool = False


FOREIGN_KEYS: dict[str, tuple[ForeignKeyRule, ...]] = {
    "routes": (ForeignKeyRule("agency_id", "agency"),),
    "calendar_dates": (ForeignKeyRule("service_id", "calendar"),),
    "stops": (ForeignKeyRule("parent_station", "stops", optional=True),),
    "trips": (
        ForeignKeyRule("route_id", "routes"),
        ForeignKeyRule("service_id", "calendar"),
    ),
    "stop_times": (
        ForeignKeyRule("trip_id", "trips"),
        ForeignKeyRule("stop_id", "stops"),
    ),
    "transfers": (
        ForeignKeyRule("from_stop_id", "stops"),
        ForeignKeyRule("to_stop_id", "stops"),
        ForeignKeyRule("from_trip_id", "trips", optional=True),
        ForeignKeyRule("to_trip_id", "trips", optional=True),
    ),
    "route_names_exceptions": (ForeignKeyRule("route_id", "routes"),),
    "directions": (ForeignKeyRule("route_id", "routes"),),
    "stop_order_exceptions": (ForeignKeyRule("stop_id", "stops"),),
}

PRIMARY_KEY_SOURCE: dict[str, str] = {
    "agency": "agency_id",
    "calendar": "service_id",
    "routes": "route_id",
    "stops": "stop_id",
    "trips": "trip_id",
}


@dataclass
class CleaningResult:
    """Counts and reasons produced while cleaning one file."""

    kept: int
    removed: int
    reasons: Counter[str]


def _read_header(path: Path, spec: TableSpec) -> tuple[list[str], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            raw = next(reader)
        except StopIteration as exc:
            raise DataValidationError(f"{path.name}: file is empty") from exc
    effective = validate_header(
        path.name, raw, spec.columns, implicit_headers=spec.implicit_headers
    )
    return raw, effective


def _accepted_rows(
    path: Path,
    spec: TableSpec,
    retained_ids: dict[str, set[str]],
):
    raw_header, header = _read_header(path, spec)
    indexes = {name: index for index, name in enumerate(header)}
    required = [column.source for column in spec.columns if not column.nullable]
    references = FOREIGN_KEYS.get(spec.table, ())
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row_number, row in enumerate(reader, start=2):
            reason = None
            if len(row) != len(header):
                reason = f"malformed width at row {row_number}"
            else:
                for column in required:
                    if not row[indexes[column]].strip():
                        reason = f"empty required column {column}"
                        break
                if reason is None:
                    for rule in references:
                        value = row[indexes[rule.column]].strip()
                        if not value and rule.optional:
                            continue
                        if value not in retained_ids[rule.parent]:
                            reason = (
                                f"orphan {rule.column}={value!r} "
                                f"(missing from {rule.parent})"
                            )
                            break
            yield row, reason


def clean_file(
    path: Path,
    spec: TableSpec,
    retained_ids: dict[str, set[str]],
    *,
    dry_run: bool,
) -> tuple[CleaningResult, set[str] | None]:
    """Audit one source file and atomically rewrite it only when needed."""
    reasons: Counter[str] = Counter()
    kept = 0
    retained: set[str] | None = set() if spec.table in PRIMARY_KEY_SOURCE else None
    _, effective_header = _read_header(path, spec)
    indexes = {name: index for index, name in enumerate(effective_header)}

    for row, reason in _accepted_rows(path, spec, retained_ids):
        if reason is not None:
            reasons[reason] += 1
            continue
        kept += 1
        if retained is not None:
            retained.add(row[indexes[PRIMARY_KEY_SOURCE[spec.table]]].strip())

    removed = sum(reasons.values())
    if removed and not dry_run:
        raw_header, _ = _read_header(path, spec)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".cleaning", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(raw_header)
                for row, reason in _accepted_rows(path, spec, retained_ids):
                    if reason is None:
                        writer.writerow(row)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return CleaningResult(kept, removed, reasons), retained


def clean_extracted_data(data_dir: Path, *, dry_run: bool = False) -> dict[str, CleaningResult]:
    """Clean all schema-backed files in dependency order."""
    retained_ids: dict[str, set[str]] = {
        table: set() for table in PRIMARY_KEY_SOURCE
    }
    results: dict[str, CleaningResult] = {}
    for spec in TABLES:
        path = data_dir / spec.filename
        if not path.is_file():
            raise DataValidationError(f"Missing required source file: {path}")
        # A self-referencing stop may appear before or after its parent. Seed
        # the candidate IDs before evaluating parent_station references.
        if spec.table == "stops":
            _, header = _read_header(path, spec)
            indexes = {name: index for index, name in enumerate(header)}
            required = [column.source for column in spec.columns if not column.nullable]
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                next(reader)
                retained_ids["stops"] = {
                    row[indexes["stop_id"]].strip()
                    for row in reader
                    if len(row) == len(header)
                    and all(row[indexes[column]].strip() for column in required)
                }
        LOGGER.info("%s %s", "Auditing" if dry_run else "Cleaning", spec.filename)
        result, retained = clean_file(path, spec, retained_ids, dry_run=dry_run)
        results[spec.table] = result
        if retained is not None:
            retained_ids[spec.table] = retained
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove incomplete/orphaned rows from extracted transit files."
    )
    parser.add_argument("--dry-run", action="store_true", help="report without changing files")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        results = clean_extracted_data(args.data_dir.resolve(), dry_run=args.dry_run)
    except (DataValidationError, OSError) as exc:
        print(f"Cleaning failed: {exc}", file=sys.stderr)
        return 1
    total_removed = 0
    for table, result in results.items():
        total_removed += result.removed
        print(f"{table}: kept {result.kept:,}, removed {result.removed:,}")
        for reason, count in result.reasons.items():
            print(f"  {count:,} x {reason}")
    mode = "would be removed" if args.dry_run else "removed"
    print(f"\nTotal: {total_removed:,} rows {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
