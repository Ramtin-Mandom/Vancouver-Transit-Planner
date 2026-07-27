"""Deterministic journey-case selection from the real GTFS database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol


@dataclass(frozen=True)
class IntegrationCase:
    case_number: int
    origin_stop_id: str
    origin_stop_name: str
    destination_stop_id: str
    destination_stop_name: str
    service_date: date
    departure_time: timedelta
    source_trip_id: str
    source_route_id: str
    source_route_name: str
    scheduled_source_arrival: timedelta


class CaseDatabase(Protocol):
    def integration_case_rows(self, limit: int) -> list[dict[str, Any]]: ...


def select_integration_cases(
    database: CaseDatabase, limit: int = 10
) -> list[IntegrationCase]:
    """Return a small real-data journey set without copying production data."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > 100:
        raise ValueError("limit may not exceed 100")
    rows = database.integration_case_rows(limit)
    return [
        IntegrationCase(
            case_number=index,
            origin_stop_id=row["origin_stop_id"],
            origin_stop_name=row["origin_stop_name"],
            destination_stop_id=row["destination_stop_id"],
            destination_stop_name=row["destination_stop_name"],
            service_date=row["service_date"],
            departure_time=row["departure_time"],
            source_trip_id=row["source_trip_id"],
            source_route_id=row["source_route_id"],
            source_route_name=row["source_route_name"],
            scheduled_source_arrival=row["scheduled_source_arrival"],
        )
        for index, row in enumerate(rows, start=1)
    ]
