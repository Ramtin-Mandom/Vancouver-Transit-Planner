"""Typed reliability-domain values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.routing.models import Itinerary


@dataclass(frozen=True)
class ScheduledStop:
    trip_id: str
    stop_id: str
    stop_sequence: int
    scheduled_arrival: timedelta
    scheduled_departure: timedelta | None = None


@dataclass(frozen=True)
class DelayObservation:
    trip_id: str
    stop_id: str
    stop_sequence: int
    service_date: date
    scheduled_arrival: timedelta
    observed_at: datetime
    delay_seconds: int
    source: str = "translink-gtfs-realtime"


@dataclass(frozen=True)
class ParseSummary:
    feed_timestamp: datetime
    observations: tuple[DelayObservation, ...]
    trip_updates_processed: int
    stop_updates_processed: int
    malformed: int
    unknown: int
    unusable_delay: int


@dataclass(frozen=True)
class CollectionSummary:
    feed_timestamp: datetime
    trip_updates_processed: int
    stop_updates_processed: int
    inserted: int
    duplicates: int
    malformed: int
    unknown: int
    unusable_delay: int


@dataclass(frozen=True)
class ReliabilityProfile:
    route_id: str | None
    stop_id: str | None
    weekday: int | None
    hour_of_day: int | None
    sample_count: int
    mean_delay_seconds: float
    delay_stddev_seconds: float | None
    p50_delay_seconds: float
    p90_delay_seconds: float
    on_time_probability: float


@dataclass(frozen=True)
class ProfileSelection:
    profile: ReliabilityProfile | None
    fallback_level: str
    insufficient_data: bool


@dataclass(frozen=True)
class AggregationSummary:
    observations_used: int
    profiles_upserted: int
    profiles_below_minimum: int
    elapsed_seconds: float


@dataclass(frozen=True)
class SimulationResult:
    completion_probability: float
    on_time_arrival_probability: float
    expected_arrival_delay_seconds: float
    p90_arrival_delay_seconds: float
    insufficient_data: bool
    fallback_levels: tuple[str, ...]


@dataclass(frozen=True)
class RankedItinerary:
    itinerary: Itinerary
    simulation: SimulationResult
    reliability_score: float
    itinerary_id: str
