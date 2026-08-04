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
    from_arrival_time: timedelta | None = None
    to_departure_time: timedelta | None = None


@dataclass(frozen=True)
class LegStop:
    """One scheduled stop within the passenger's portion of a trip."""

    stop: Stop
    stop_sequence: int
    arrival_time: timedelta | None
    departure_time: timedelta | None


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
    stops: tuple[LegStop, ...] = ()


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
class SearchDiagnosticTimings:
    gtfs_version_lookup_ms: float = 0.0
    static_snapshot_build_ms: float = 0.0
    daily_departure_index_build_ms: float = 0.0
    daily_departure_query_ms: float = 0.0
    daily_departure_grouping_ms: float = 0.0
    daily_departure_sorting_ms: float = 0.0
    skytrain_preload_ms: float = 0.0
    reliability_snapshot_build_ms: float = 0.0
    index_building_ms: float = 0.0
    initial_preload_ms: float = 0.0
    eager_trip_preload_ms: float = 0.0
    frontier_trip_loading_ms: float = 0.0
    frontier_trip_query_ms: float = 0.0
    frontier_trip_indexing_ms: float = 0.0
    reconstruction_trip_loading_ms: float = 0.0
    search_cpu_excluding_frontier_io_ms: float = 0.0
    preload_total_ms: float = 0.0
    departures_preload_ms: float = 0.0
    transfers_preload_ms: float = 0.0
    trip_connections_preload_ms: float = 0.0
    reliability_preload_ms: float = 0.0
    search_cpu_ms: float = 0.0
    fallback_query_ms: float = 0.0
    statement_timeout_setup_ms: float = 0.0
    stop_lookup_ms: float = 0.0
    active_service_lookup_ms: float = 0.0
    departure_queries_ms: float = 0.0
    trip_queries_ms: float = 0.0
    transfer_queries_ms: float = 0.0
    profile_resolution_ms: float = 0.0
    reconstruction_queries_ms: float = 0.0
    queue_processing_ms: float = 0.0
    transfer_expansion_ms: float = 0.0
    departure_scanning_ms: float = 0.0
    trip_scanning_ms: float = 0.0
    label_processing_ms: float = 0.0
    destination_filtering_ms: float = 0.0
    reconstruction_ms: float = 0.0
    measured_search_ms: float = 0.0
    unclassified_search_ms: float = 0.0


@dataclass(frozen=True)
class SearchDiagnosticCounters:
    algorithm: str = "baseline"
    requested_algorithm: str = "baseline"
    executed_algorithm: str = "baseline"
    states_pushed: int = 0
    states_popped: int = 0
    states_reopened: int = 0
    transfer_edges_examined: int = 0
    heuristic_evaluations: int = 0
    zero_heuristic_fallbacks: int = 0
    geographic_heuristic_enabled: bool = False
    validated_maximum_speed_mps: float | None = None
    heuristic_fallback_reason: str | None = None
    heuristic_cache_hits: int = 0
    final_arrival_cost: int = 0
    candidate_trip_ids_from_frontier: int = 0
    unique_frontier_trips_requested: int = 0
    unique_frontier_trips_loaded: int = 0
    frontier_connections_loaded: int = 0
    frontier_trip_batch_query_count: int = 0
    frontier_trip_batch_sizes: tuple[int, ...] = ()
    average_frontier_trip_batch_size: float = 0.0
    maximum_frontier_trip_batch_size: int = 0
    single_trip_batch_count: int = 0
    repeated_trip_fetch_attempts: int = 0
    known_empty_trip_count: int = 0
    reconstruction_trip_batch_count: int = 0
    eager_trips_avoided: int = 0
    eager_connections_avoided: int = 0
    unexpected_nonbulk_trip_queries: int = 0
    queue_pushes: int = 0
    queue_pops: int = 0
    stale_labels_skipped: int = 0
    labels_created: int = 0
    labels_accepted: int = 0
    labels_pruned: int = 0
    dominance_checks: int = 0
    maximum_queue_size: int = 0
    maximum_labels_in_bucket: int = 0
    total_label_buckets: int = 0
    stops_expanded: int = 0
    transfer_rules_examined: int = 0
    walking_transfer_labels_created: int = 0
    departures_examined: int = 0
    boardable_departures: int = 0
    trips_examined: int = 0
    connections_examined: int = 0
    destination_labels_found: int = 0
    alternatives_reconstructed: int = 0
    alternatives_returned: int = 0
    unique_heuristic_calculations: int = 0
    heuristic_cache_hits: int = 0
    rounds_executed: int = 0
    route_patterns_collected: int = 0
    route_pattern_scans: int = 0
    unique_routes_scanned_per_round: tuple[int, ...] = ()
    trips_considered: int = 0
    trips_boarded: int = 0
    stop_time_entries_scanned: int = 0
    labels_inserted: int = 0
    maximum_pareto_bag_size: int = 0
    marked_stops_per_round: tuple[int, ...] = ()
    transfer_edges_relaxed: int = 0
    candidate_itineraries: int = 0
    candidate_truncated: bool = False
    candidate_collection_complete: bool = False
    resource_limit_reached: bool = False
    termination_reason: str | None = None


@dataclass(frozen=True)
class SearchCacheStatistics:
    first_request_cache_hits: int = 0
    first_request_cache_misses: int = 0
    single_flight_wait_count: int = 0
    trip_request_cache_hits: int = 0
    trip_shared_cache_hits: int = 0
    trip_shared_cache_misses: int = 0
    trip_negative_cache_hits: int = 0
    daily_index_hits: int = 0
    daily_index_misses: int = 0
    reliability_cache_hits: int = 0
    reliability_cache_misses: int = 0
    heuristic_cache_hits: int = 0
    heuristic_cache_misses: int = 0
    response_cache_hit: bool = False
    cache_evictions: int = 0
    shared_cache_memory_estimate_bytes: int = 0
    bulk_departure_query_count: int = 0
    bulk_transfer_query_count: int = 0
    bulk_trip_query_count: int = 0
    bulk_profile_query_count: int = 0
    unique_departures_loaded: int = 0
    unique_trips_loaded: int = 0
    unique_connections_loaded: int = 0
    unique_transfers_loaded: int = 0
    request_index_memory_estimate_bytes: int = 0
    unexpected_queries_during_search: int = 0
    departure_query_count: int = 0
    departure_rows_loaded: int = 0
    departure_cache_hits: int = 0
    departure_cache_misses: int = 0
    trip_query_count: int = 0
    trip_connection_rows_loaded: int = 0
    trip_cache_hits: int = 0
    trip_cache_misses: int = 0
    transfer_query_count: int = 0
    transfer_rows_loaded: int = 0
    transfer_cache_hits: int = 0
    transfer_cache_misses: int = 0
    profile_resolver_calls: int = 0
    profile_cache_hits: int = 0
    profile_cache_misses: int = 0


@dataclass(frozen=True)
class SearchDiagnostics:
    timings_ms: SearchDiagnosticTimings
    counters: SearchDiagnosticCounters
    cache_statistics: SearchCacheStatistics
    profiling_overhead_note: str = (
        "Query/profile timings overlap their enclosing expansion and scanning "
        "wall-clock categories; measured_search_ms is authoritative and the "
        "individual fields must not be summed as an exclusive total."
    )


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
    diagnostics: SearchDiagnostics | None = None
