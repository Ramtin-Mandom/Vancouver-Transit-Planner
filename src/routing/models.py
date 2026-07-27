"""Typed values shared by the routing database and planner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class Stop:
    stop_id: str
    stop_name: str
    stop_code: str | None = None
    stop_lat: Decimal | None = None
    stop_lon: Decimal | None = None


@dataclass(frozen=True)
class Connection:
    """One scheduled hop between consecutive stops on a trip."""

    trip_id: str
    service_id: str
    route_id: str
    route_name: str
    from_stop_id: str
    to_stop_id: str
    departure_time: timedelta
    arrival_time: timedelta
    from_stop_sequence: int
    to_stop_sequence: int


@dataclass(frozen=True)
class RouteLeg:
    """A contiguous ride on one vehicle."""

    trip_id: str
    route_id: str
    route_name: str
    origin: Stop
    destination: Stop
    departure_time: timedelta
    arrival_time: timedelta


@dataclass(frozen=True)
class Itinerary:
    origin: Stop
    destination: Stop
    service_date: date
    departure_time: timedelta
    arrival_time: timedelta
    legs: tuple[RouteLeg, ...]

    @property
    def transfer_count(self) -> int:
        return max(0, len(self.legs) - 1)

    @property
    def total_scheduled_travel_time(self) -> timedelta:
        return self.arrival_time - self.departure_time
