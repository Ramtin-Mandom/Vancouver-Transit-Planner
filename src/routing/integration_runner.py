"""Run real-GTFS journey checks against the configured PostgreSQL database."""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from time import perf_counter
from typing import Protocol

from src.data_ingestion.config import ConfigurationError

from .integration_cases import IntegrationCase, select_integration_cases
from .integration_report import (
    IntegrationResult,
    IntegrationSummary,
    print_case_result,
    print_summary,
)
from .models import Itinerary, RouteLeg

MAX_PLANNER_SECONDS = 30.0


class ReferenceDatabase(Protocol):
    def integration_case_rows(self, limit: int): ...

    def itinerary_leg_exists(self, leg: RouteLeg) -> bool: ...


class Planner(Protocol):
    def plan(self, origin_stop_id, destination_stop_id, service_date, departure_time):
        ...


def validate_itinerary(
    database: ReferenceDatabase,
    case: IntegrationCase,
    requested_departure: timedelta,
    itinerary: Itinerary | None,
) -> list[str]:
    failures: list[str] = []
    if itinerary is None:
        return ["planner returned no itinerary"]
    if itinerary.origin.stop_id != case.origin_stop_id:
        failures.append("itinerary origin does not match the requested origin")
    if itinerary.destination.stop_id != case.destination_stop_id:
        failures.append("itinerary destination does not match the requested destination")
    if itinerary.departure_time != requested_departure:
        failures.append("itinerary requested departure time changed")
    if itinerary.arrival_time < requested_departure:
        failures.append("itinerary arrives before the requested departure")
    if not itinerary.legs:
        failures.append("itinerary contains no route legs")
    else:
        if itinerary.legs[0].departure_time < requested_departure:
            failures.append("first leg departs before the requested departure")
        previous_arrival: timedelta | None = None
        for number, leg in enumerate(itinerary.legs, start=1):
            if leg.arrival_time < leg.departure_time:
                failures.append(f"leg {number} arrives before it departs")
            if previous_arrival is not None and leg.departure_time < previous_arrival:
                failures.append(f"leg {number} overlaps the previous leg")
            previous_arrival = leg.arrival_time
            if not database.itinerary_leg_exists(leg):
                failures.append(
                    f"leg {number} trip/route/stops/times are absent from GTFS"
                )
        if itinerary.legs[-1].destination.stop_id != case.destination_stop_id:
            failures.append("final leg does not arrive at the requested destination")
    expected_transfers = max(0, len(itinerary.legs) - 1)
    if itinerary.transfer_count != expected_transfers:
        failures.append("transfer count is inconsistent with the route legs")
    if (
        itinerary.total_scheduled_travel_time
        != itinerary.arrival_time - itinerary.departure_time
    ):
        failures.append("total scheduled travel time is inconsistent")
    return failures


def run_cases(
    database: ReferenceDatabase,
    planner: Planner,
    *,
    limit: int = 10,
    departure_buffer_minutes: int = 1,
    fail_fast: bool = False,
    verbose: bool = False,
) -> tuple[list[IntegrationResult], IntegrationSummary]:
    if departure_buffer_minutes < 0:
        raise ValueError("departure buffer minutes may not be negative")
    started = perf_counter()
    cases = select_integration_cases(database, limit)
    results: list[IntegrationResult] = []
    for case in cases:
        requested = case.departure_time - timedelta(
            minutes=departure_buffer_minutes
        )
        case_started = perf_counter()
        itinerary = None
        failures: list[str] = []
        error = None
        try:
            itinerary = planner.plan(
                case.origin_stop_id,
                case.destination_stop_id,
                case.service_date,
                requested,
            )
            elapsed = perf_counter() - case_started
            failures.extend(
                validate_itinerary(database, case, requested, itinerary)
            )
            if elapsed > MAX_PLANNER_SECONDS:
                failures.append(
                    f"planner call exceeded {MAX_PLANNER_SECONDS:.0f} seconds"
                )
        except Exception as exc:  # continue the diagnostic run case-by-case
            elapsed = perf_counter() - case_started
            error = f"{type(exc).__name__}: {exc}"
        result = IntegrationResult(
            case=case,
            requested_departure=requested,
            itinerary=itinerary,
            failures=tuple(failures),
            elapsed_seconds=elapsed,
            error=error,
        )
        results.append(result)
        print_case_result(result, verbose=verbose)
        if fail_fast and not result.passed:
            break

    passed = sum(result.passed for result in results)
    failed = len(results) - passed
    skipped = max(0, limit - len(results))
    summary = IntegrationSummary(
        passed=passed,
        failed=failed,
        skipped=skipped,
        executed=len(results),
        elapsed_seconds=perf_counter() - started,
    )
    print_summary(summary)
    return results, summary


def exit_status(summary: IntegrationSummary) -> int:
    return 0 if summary.failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the route planner with real PostgreSQL GTFS journeys."
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--departure-buffer-minutes", type=int, default=1)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        from .database import TransitDatabase
        from .planner import TransitPlanner

        database = TransitDatabase()
        planner = TransitPlanner(database)
        _, summary = run_cases(
            database,
            planner,
            limit=args.limit,
            departure_buffer_minutes=args.departure_buffer_minutes,
            fail_fast=args.fail_fast,
            verbose=args.verbose,
        )
    except (ConfigurationError, ImportError, OSError) as exc:
        print(f"Integration setup failed: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Invalid integration option: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Case exceptions are handled by run_cases. Reaching here means case
        # discovery or the database connection failed before execution.
        print(f"Integration database failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return exit_status(summary)


if __name__ == "__main__":
    raise SystemExit(main())
