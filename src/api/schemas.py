"""API-specific request and response models."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.reliability.policy import DEFAULT_MINIMUM_SAMPLES

from src.routing.cli import parse_gtfs_time
from src.routing.route_results import RoutingPreferences
from src.routing.cache import DEFAULT_ROUTING_CACHE_MODE


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StopResponse(ApiModel):
    stop_id: str
    stop_code: str | None
    stop_name: str
    latitude: float | None
    longitude: float | None


class LegStopResponse(ApiModel):
    stop: StopResponse
    stop_sequence: int
    arrival_time: str | None
    departure_time: str | None


class RouteLegResponse(ApiModel):
    trip_id: str
    route_id: str
    route_name: str
    direction_id: int | None
    origin: StopResponse
    destination: StopResponse
    departure_time: str
    arrival_time: str
    duration_seconds: int
    stops: list[LegStopResponse]


class RouteAlternativeResponse(ApiModel):
    rank: int
    departure_time: str
    arrival_time: str
    duration_seconds: int
    duration_display: str
    transfer_count: int
    route_reliability: float
    combined_score: float
    speed_component: float
    fallback_levels: list[str]
    insufficient_data: bool
    legs: list[RouteLegResponse]


class SearchTimingResponse(ApiModel):
    data_loading_ms: float
    search_ms: float
    ranking_ms: float
    total_ms: float


class SearchDiagnosticTimingsResponse(ApiModel):
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


class SearchDiagnosticCountersResponse(ApiModel):
    algorithm: str = "baseline"
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


class SearchCacheStatisticsResponse(ApiModel):
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


class SearchDiagnosticsResponse(ApiModel):
    timings_ms: SearchDiagnosticTimingsResponse
    counters: SearchDiagnosticCountersResponse
    cache_statistics: SearchCacheStatisticsResponse
    profiling_overhead_note: str


class RoutePlanResponse(ApiModel):
    origin: StopResponse
    destination: StopResponse
    service_date: date
    requested_departure_time: str
    alternatives: list[RouteAlternativeResponse]
    timing: SearchTimingResponse
    diagnostics: SearchDiagnosticsResponse | None = None


class RoutePlanRequest(ApiModel):
    origin_stop_id: str
    destination_stop_id: str
    departure_time: str
    algorithm: Literal["baseline", "dijkstra", "astar", "mc_raptor"] = "astar"
    cache_mode: Literal["request", "shared"] = DEFAULT_ROUTING_CACHE_MODE
    route_number: int = Field(default=5, ge=1, le=5)
    minimum_samples: int = Field(default=DEFAULT_MINIMUM_SAMPLES, ge=1)
    max_extra_minutes: int = Field(default=30, ge=0, le=120)
    search_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    reliability_effect: float = Field(default=0.5, ge=0)
    travel_time_effect: float = Field(default=0.5, ge=0)
    transfer_effect: float = Field(default=0.0, ge=0)
    include_diagnostics: bool = False

    @model_validator(mode="after")
    def validate_route_request(self) -> "RoutePlanRequest":
        self.origin_stop_id = self.origin_stop_id.strip()
        self.destination_stop_id = self.destination_stop_id.strip()
        if not self.origin_stop_id or not self.destination_stop_id:
            raise ValueError("origin and destination stop IDs must not be blank")
        if self.origin_stop_id == self.destination_stop_id:
            raise ValueError("origin and destination stop IDs must be different")
        self.parsed_departure_time()
        self.routing_preferences()
        return self

    def parsed_departure_time(self) -> timedelta:
        try:
            return parse_gtfs_time(self.departure_time.strip())
        except (argparse.ArgumentTypeError, TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    def routing_preferences(self) -> RoutingPreferences:
        return RoutingPreferences(
            reliability_effect=self.reliability_effect,
            travel_time_effect=self.travel_time_effect,
            transfer_effect=self.transfer_effect,
        )
