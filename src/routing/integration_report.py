"""Terminal output and result values for real-data routing checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .cli import format_gtfs_time
from .integration_cases import IntegrationCase
from .models import Itinerary


@dataclass(frozen=True)
class IntegrationResult:
    case: IntegrationCase
    requested_departure: timedelta
    itinerary: Itinerary | None
    failures: tuple[str, ...]
    elapsed_seconds: float
    validation_seconds: float = 0.0
    formatting_seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures


@dataclass(frozen=True)
class IntegrationSummary:
    passed: int
    failed: int
    skipped: int
    executed: int
    elapsed_seconds: float
    initialization_seconds: float = 0.0
    gtfs_loading_seconds: float = 0.0
    structure_construction_seconds: float = 0.0
    route_total_seconds: float = 0.0
    route_min_seconds: float = 0.0
    route_max_seconds: float = 0.0
    route_average_seconds: float = 0.0
    formatting_seconds: float = 0.0


def print_case_result(result: IntegrationResult, verbose: bool = False) -> None:
    case = result.case
    status = "PASS" if result.passed else "FAIL"
    print(f"\nTest {case.case_number}: {status} ({result.elapsed_seconds:.3f}s)")
    print(f"  Service date: {case.service_date.isoformat()}")
    print(f"  Requested departure: {format_gtfs_time(result.requested_departure)}")
    print(
        f"  Origin: {case.origin_stop_id} — {case.origin_stop_name}\n"
        f"  Destination: {case.destination_stop_id} — {case.destination_stop_name}"
    )
    print(
        f"  Source: route {case.source_route_name} ({case.source_route_id}), "
        f"trip {case.source_trip_id}, arrival "
        f"{format_gtfs_time(case.scheduled_source_arrival)}"
    )
    if result.error:
        print(f"  Error: {result.error}")
    itinerary = result.itinerary
    if itinerary is not None:
        for number, leg in enumerate(itinerary.legs, start=1):
            print(
                f"  Leg {number}: {leg.route_name}, trip {leg.trip_id}\n"
                f"    {leg.origin.stop_id} — {leg.origin.stop_name} "
                f"{format_gtfs_time(leg.departure_time)} -> "
                f"{leg.destination.stop_id} — {leg.destination.stop_name} "
                f"{format_gtfs_time(leg.arrival_time)}"
            )
        print(f"  Transfers: {itinerary.transfer_count}")
        print(
            "  Total scheduled travel time: "
            f"{format_gtfs_time(itinerary.total_scheduled_travel_time)}"
        )
    for failure in result.failures:
        print(f"  Validation failure: {failure}")
    if verbose and result.passed:
        print("  Validation: all itinerary invariants and database references passed")


def print_summary(summary: IntegrationSummary) -> None:
    print("\nIntegration summary")
    print(f"  Passed: {summary.passed}")
    print(f"  Failed: {summary.failed}")
    print(f"  Skipped: {summary.skipped}")
    print(f"  Total executed: {summary.executed}")
    print(f"  One-time initialization: {summary.initialization_seconds:.3f}s")
    print(f"  GTFS case loading: {summary.gtfs_loading_seconds:.3f}s")
    print(
        "  Graph/routing structure construction: "
        f"{summary.structure_construction_seconds:.3f}s"
    )
    print(f"  Minimum route-search time: {summary.route_min_seconds:.3f}s")
    print(f"  Maximum route-search time: {summary.route_max_seconds:.3f}s")
    print(f"  Average route-search time: {summary.route_average_seconds:.3f}s")
    print(f"  Total route-search time: {summary.route_total_seconds:.3f}s")
    print(f"  Result formatting time: {summary.formatting_seconds:.3f}s")
    print(f"  Total execution time: {summary.elapsed_seconds:.3f}s")
