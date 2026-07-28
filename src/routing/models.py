"""Typed values shared by the routing database and planner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.reliability.models import ProfileSelection


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
    direction_id: int | None = None


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
    direction_id: int | None = None


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


@dataclass(frozen=True)
class SearchTiming:
    data_loading_ms: float
    search_ms: float
    ranking_ms: float
    total_ms: float


@dataclass(frozen=True)
class ReliableAlternative:
    """An itinerary with the analytic reliability used during its search."""

    itinerary: Itinerary
    route_reliability: float
    reliability_cost: float
    profile_selections: tuple["ProfileSelection", ...]
    speed_component: float = 0.0
    combined_score: float = 0.0

    @property
    def insufficient_data(self) -> bool:
        return any(item.insufficient_data for item in self.profile_selections)

    @property
    def fallback_levels(self) -> tuple[str, ...]:
        return tuple(item.fallback_level for item in self.profile_selections)


@dataclass(frozen=True)
class ReliableSearchResult:
    alternatives: tuple[ReliableAlternative, ...]
    timing: SearchTiming
    labels_pruned: int
