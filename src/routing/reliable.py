"""Bounded time-dependent Pareto search for reliability-aware alternatives.

Each boarding contributes the selected profile's sample-adjusted
``reliability_probability``.
Leg probabilities are assumed independent and are multiplied (implemented as
the sum of negative logarithms). Transfer success is deliberately not added:
the same delay observations feed the leg profiles, so doing both here would
risk counting one reliability signal twice.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import count
from time import perf_counter
from typing import Any

from src.reliability.classification import TIME_WINDOWS, time_window

from .models import (
    Connection,
    Itinerary,
    ReliableAlternative,
    ReliableSearchResult,
    RouteLeg,
    SearchTiming,
    SearchCacheStatistics,
    SearchDiagnosticCounters,
    SearchDiagnostics,
    SearchDiagnosticTimings,
    Stop,
)
from .route_results import itinerary_identity
from .reconstruction import build_leg_stops, load_connection_stops
from .search_data import RequestTripConnectionLoader, SearchDataIndex

EPSILON = 1e-9
DEFAULT_MAX_TRANSFERS = 3
DEFAULT_SEARCH_HORIZON_MINUTES = 180
DEFAULT_TIMEOUT_SECONDS = 30.0
logger = logging.getLogger(__name__)


class ReliableSearchTimeout(RuntimeError):
    """Raised when a reliable search exceeds its configured wall-clock limit."""

    def __init__(self, message: str, diagnostics: SearchDiagnostics | None = None,
                 log_context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics
        self.log_context = log_context or {}


@dataclass(frozen=True)
class _Label:
    stop_id: str
    arrival: timedelta
    reliability_cost: float
    transfers: int
    current_trip_id: str | None
    rides: tuple[Connection, ...]
    selections: tuple[Any, ...]
    visited: frozenset[str]


def _dominates(left: _Label, right: _Label) -> bool:
    """Safe dominance for labels with compatible current-trip state."""
    if left.stop_id != right.stop_id or left.current_trip_id != right.current_trip_id:
        return False
    no_worse = (
        left.arrival <= right.arrival
        and left.reliability_cost <= right.reliability_cost
        and left.transfers <= right.transfers
    )
    return no_worse and (
        left.arrival < right.arrival
        or left.reliability_cost < right.reliability_cost
        or left.transfers < right.transfers
    )


class ParetoTransitSearch:
    def __init__(self, database: Any, calendar: Any, resolver: Any) -> None:
        self.database = database
        self.calendar = calendar
        self.resolver = resolver

    def search(
        self,
        origin_stop_id: str,
        destination_stop_id: str,
        service_date: date,
        departure_time: timedelta,
        *,
        limit: int | None = None,
        max_transfers: int = DEFAULT_MAX_TRANSFERS,
        max_extra_minutes: int = 30,
        search_horizon_minutes: int = DEFAULT_SEARCH_HORIZON_MINUTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        include_diagnostics: bool = False,
        trip_loading_mode: str = "eager",
    ) -> ReliableSearchResult:
        if limit is not None and limit < 1:
            raise ValueError("candidate limit must be positive")
        if max_transfers < 0 or max_extra_minutes < 0:
            raise ValueError("invalid reliable-search bounds")
        if search_horizon_minutes < 1:
            raise ValueError("search horizon must be positive")
        if timeout_seconds <= 0:
            raise ValueError("search timeout must be positive")
        if trip_loading_mode not in {"frontier", "eager"}:
            raise ValueError("trip_loading_mode must be 'frontier' or 'eager'")
        started = perf_counter()
        deadline = started + timeout_seconds
        timings = (
            vars(SearchDiagnosticTimings()).copy()
            if include_diagnostics else {}
        )
        counters = (
            vars(SearchDiagnosticCounters()).copy()
            if include_diagnostics else {}
        )
        caches = (
            vars(SearchCacheStatistics()).copy()
            if include_diagnostics else {}
        )

        def snapshot() -> SearchDiagnostics | None:
            if not include_diagnostics:
                return None
            try:
                if frontier_loader is not None:
                    timings["frontier_trip_query_ms"] = frontier_loader.query_ms
                    timings["frontier_trip_indexing_ms"] = frontier_loader.indexing_ms
                    counters["unique_frontier_trips_requested"] = len(
                        frontier_loader.loaded_trip_ids
                    )
                    counters["unique_frontier_trips_loaded"] = len(
                        frontier_loader.loaded_trip_ids
                        - frontier_loader.known_empty_trip_ids
                    )
                    counters["frontier_connections_loaded"] = (
                        frontier_loader.connections_loaded
                    )
                    counters["frontier_trip_batch_query_count"] = (
                        frontier_loader.query_count
                    )
                    counters["frontier_trip_batch_sizes"] = tuple(
                        frontier_loader.batch_sizes
                    )
                    counters["known_empty_trip_count"] = len(
                        frontier_loader.known_empty_trip_ids
                    )
            except NameError:
                pass
            elapsed = (perf_counter() - started) * 1000
            timings["measured_search_ms"] = elapsed
            classified = sum(value for name, value in timings.items() if name not in {
                "measured_search_ms", "unclassified_search_ms"
            })
            timings["unclassified_search_ms"] = max(0.0, elapsed - classified)
            try:
                counters["total_label_buckets"] = len(labels)
            except NameError:
                counters["total_label_buckets"] = 0
            return SearchDiagnostics(
                SearchDiagnosticTimings(**timings),
                SearchDiagnosticCounters(**counters),
                SearchCacheStatistics(**caches),
            )

        def check_deadline() -> None:
            if perf_counter() >= deadline:
                diagnostics = snapshot()
                context = {
                    "origin_stop_id": origin_stop_id,
                    "destination_stop_id": destination_stop_id,
                    "service_date": str(service_date),
                    "departure_time": str(departure_time),
                    "timed_out": True,
                    "diagnostics": diagnostics,
                }
                if include_diagnostics:
                    logger.warning("Profiled reliable search timed out", extra=context)
                raise ReliableSearchTimeout(
                    f"reliable search exceeded {timeout_seconds:g} seconds; "
                    "try a shorter horizon or fewer transfers", diagnostics, context
                )

        setup_started = perf_counter() if include_diagnostics else 0.0
        configure_timeout = getattr(self.database, "set_statement_timeout", None)
        if callable(configure_timeout):
            configure_timeout(max(1, int(timeout_seconds * 1000)))
        resolver_timeout = getattr(self.resolver, "set_statement_timeout", None)
        if callable(resolver_timeout):
            resolver_timeout(max(1, int(timeout_seconds * 1000)))
        if include_diagnostics:
            timings["statement_timeout_setup_ms"] += (perf_counter() - setup_started) * 1000
        lookup_started = perf_counter() if include_diagnostics else 0.0
        origin = self.database.find_stop(origin_stop_id)
        check_deadline()
        destination = self.database.find_stop(destination_stop_id)
        if include_diagnostics:
            timings["stop_lookup_ms"] += (perf_counter() - lookup_started) * 1000
        check_deadline()
        if origin is None or destination is None:
            missing = origin_stop_id if origin is None else destination_stop_id
            raise ValueError(f"unknown stop_id: {missing}")
        horizon = departure_time + timedelta(minutes=search_horizon_minutes)
        active_lookup = getattr(self.database, "active_service_ids", None)
        active_started = perf_counter() if include_diagnostics else 0.0
        active = active_lookup(service_date) if callable(active_lookup) else None
        if include_diagnostics:
            timings["active_service_lookup_ms"] += (perf_counter() - active_started) * 1000
        departure_cache: dict[str, list[Connection]] = {}
        trip_cache: dict[tuple[str, int], list[Connection]] = {}
        transfer_cache: dict[str, list[dict[str, Any]]] = {}
        profile_cache: dict[tuple[str, str, int, int], Any] = {}
        data_index: SearchDataIndex | None = None
        frontier_loader: RequestTripConnectionLoader | None = None
        bulk_departures = getattr(self.database, "bulk_departures_in_window", None)
        bulk_transfers = getattr(self.database, "bulk_transfers", None)
        bulk_trips = getattr(self.database, "bulk_trip_connections", None)
        if all(callable(item) for item in (bulk_departures, bulk_transfers, bulk_trips)):
            preload_started = perf_counter()
            coordinated_loader = getattr(self.database, "bulk_search_data", None)
            if callable(coordinated_loader):
                (
                    loaded_departures,
                    loaded_transfers,
                    loaded_connections,
                    transit_phase_timings,
                ) = coordinated_loader(
                    departure_time, horizon, service_ids=active,
                    trip_batch_size=2000,
                    include_trip_connections=trip_loading_mode == "eager",
                )
                if include_diagnostics:
                    timings.update(transit_phase_timings)
            else:
                phase_started = perf_counter()
                loaded_departures = bulk_departures(
                    departure_time, horizon, service_ids=active
                )
                departure_ms = (perf_counter() - phase_started) * 1000
                phase_started = perf_counter()
                loaded_transfers = bulk_transfers()
                transfer_ms = (perf_counter() - phase_started) * 1000
                phase_started = perf_counter()
                loaded_connections = (
                    bulk_trips({item.trip_id for item in loaded_departures})
                    if trip_loading_mode == "eager" else []
                )
                if include_diagnostics:
                    timings["departures_preload_ms"] = departure_ms
                    timings["transfers_preload_ms"] = transfer_ms
                    timings["trip_connections_preload_ms"] = (
                        perf_counter() - phase_started
                    ) * 1000
            trip_ids = {item.trip_id for item in loaded_departures}
            if include_diagnostics:
                caches["bulk_departure_query_count"] = (
                    0 if active == set() else 1
                )
                caches["unique_departures_loaded"] = len(set(loaded_departures))
                caches["bulk_transfer_query_count"] = 1
                caches["unique_transfers_loaded"] = len(loaded_transfers)
                caches["bulk_trip_query_count"] = (
                    (len(trip_ids) + 1999) // 2000
                    if trip_loading_mode == "eager" and trip_ids else 0
                )
                caches["unique_trips_loaded"] = len(trip_ids)
                caches["unique_connections_loaded"] = len(set(loaded_connections))
            phase_started = perf_counter()
            preload_profiles = getattr(self.resolver, "preload", None)
            if callable(preload_profiles):
                profile_keys = {
                    (item.route_id, item.direction_id, window_name)
                    for item in loaded_departures
                    for window_name, _, _ in TIME_WINDOWS
                }
                profile_query_count = preload_profiles(profile_keys)
                if include_diagnostics:
                    caches["bulk_profile_query_count"] = profile_query_count
            if include_diagnostics:
                timings["reliability_preload_ms"] = (
                    perf_counter() - phase_started
                ) * 1000
            data_index = SearchDataIndex.build(
                loaded_departures, loaded_connections, loaded_transfers
            )
            if trip_loading_mode == "frontier":
                frontier_loader = RequestTripConnectionLoader(self.database)
            if include_diagnostics:
                timings["initial_preload_ms"] = timings["preload_total_ms"] = (
                    perf_counter() - preload_started
                ) * 1000
                timings["eager_trip_preload_ms"] = (
                    timings["trip_connections_preload_ms"]
                    if trip_loading_mode == "eager" else 0.0
                )
                timings["preload_total_ms"] = (perf_counter() - preload_started) * 1000
                caches["request_index_memory_estimate_bytes"] = (
                    data_index.memory_estimate_bytes
                )
        loaded_at = perf_counter()

        def departures(stop_id: str) -> list[Connection]:
            check_deadline()
            if data_index is not None:
                if include_diagnostics:
                    caches["departure_cache_hits"] += 1
                return list(data_index.departures(stop_id, departure_time, horizon))
            if stop_id in departure_cache:
                if include_diagnostics:
                    caches["departure_cache_hits"] += 1
                return departure_cache[stop_id]
            query_started = perf_counter() if include_diagnostics else 0.0
            if include_diagnostics:
                caches["departure_cache_misses"] += 1
            bounded = getattr(self.database, "departures_in_window", None)
            if callable(bounded):
                rows = bounded(stop_id, departure_time, horizon, service_ids=active)
            else:
                rows, offset = [], 0
                while True:
                    batch = self.database.departures_from(
                        stop_id, departure_time, limit=256, offset=offset,
                        service_ids=active,
                    )
                    rows.extend(item for item in batch if item.departure_time <= horizon)
                    if len(batch) < 256 or (batch and batch[-1].departure_time > horizon):
                        break
                    offset += 256
            departure_cache[stop_id] = rows
            if include_diagnostics:
                caches["departure_query_count"] += 1
                caches["departure_rows_loaded"] += len(rows)
                timings["departure_queries_ms"] += (perf_counter() - query_started) * 1000
            check_deadline()
            return rows

        def trip(first: Connection) -> list[Connection]:
            check_deadline()
            key = (first.trip_id, first.from_stop_sequence)
            if frontier_loader is not None:
                if not frontier_loader.is_loaded(first.trip_id):
                    frontier_loader.ensure_loaded((first.trip_id,))
                if include_diagnostics:
                    caches["trip_cache_hits"] += 1
                return [
                    item for item in frontier_loader.connections_for(first.trip_id)
                    if item.from_stop_sequence >= first.from_stop_sequence
                ]
            if data_index is not None:
                if include_diagnostics:
                    caches["trip_cache_hits"] += 1
                return list(data_index.trip_connections(*key))
            if key not in trip_cache:
                query_started = perf_counter() if include_diagnostics else 0.0
                trip_cache[key] = self.database.trip_connections(*key)
                if include_diagnostics:
                    caches["trip_cache_misses"] += 1
                    caches["trip_query_count"] += 1
                    caches["trip_connection_rows_loaded"] += len(trip_cache[key])
                    timings["trip_queries_ms"] += (perf_counter() - query_started) * 1000
            elif include_diagnostics:
                caches["trip_cache_hits"] += 1
            return trip_cache[key]

        def transfers(stop_id: str) -> list[dict[str, Any]]:
            check_deadline()
            if data_index is not None:
                if include_diagnostics:
                    caches["transfer_cache_hits"] += 1
                return list(data_index.transfers(stop_id))
            if stop_id not in transfer_cache:
                query_started = perf_counter() if include_diagnostics else 0.0
                transfer_cache[stop_id] = self.database.transfers_from(stop_id)
                if include_diagnostics:
                    caches["transfer_cache_misses"] += 1
                    caches["transfer_query_count"] += 1
                    caches["transfer_rows_loaded"] += len(transfer_cache[stop_id])
                    timings["transfer_queries_ms"] += (perf_counter() - query_started) * 1000
            elif include_diagnostics:
                caches["transfer_cache_hits"] += 1
            return transfer_cache[stop_id]

        def profile(connection: Connection, alight_stop: str):
            check_deadline()
            key = (
                connection.route_id,
                connection.direction_id,
                connection.arrival_time,
            )
            if key not in profile_cache:
                profile_started = perf_counter() if include_diagnostics else 0.0
                if include_diagnostics:
                    caches["profile_cache_misses"] += 1
                    caches["profile_resolver_calls"] += 1
                try:
                    profile_cache[key] = self.resolver.resolve(*key)
                except TypeError:
                    # Compatibility for external resolvers using the deprecated
                    # stop/weekday/hour interface.
                    hour = int(connection.arrival_time.total_seconds() // 3600) % 24
                    profile_cache[key] = self.resolver.resolve(
                        connection.route_id, alight_stop,
                        service_date.weekday(), hour,
                    )
                if include_diagnostics:
                    timings["profile_resolution_ms"] += (perf_counter() - profile_started) * 1000
                check_deadline()
            elif include_diagnostics:
                caches["profile_cache_hits"] += 1
            return profile_cache[key]

        initial = _Label(
            origin_stop_id, departure_time, 0.0, 0, None, (), (),
            frozenset((origin_stop_id,)),
        )
        labels: dict[tuple[str, str | None], list[_Label]] = {
            (origin_stop_id, None): [initial]
        }
        queue: list[tuple[timedelta, float, int, int, _Label]] = []
        serial = count()
        heapq.heappush(queue, (initial.arrival, 0.0, 0, next(serial), initial))
        if include_diagnostics:
            counters.update(queue_pushes=1, labels_created=1, labels_accepted=1,
                            maximum_queue_size=1, maximum_labels_in_bucket=1)
        destinations: list[_Label] = []
        arrival_cutoff: timedelta | None = None
        pruned = 0

        def add(label: _Label) -> None:
            nonlocal pruned, arrival_cutoff
            key = (label.stop_id, label.current_trip_id)
            bucket = labels.setdefault(key, [])
            add_started = perf_counter() if include_diagnostics else 0.0
            if include_diagnostics:
                counters["labels_created"] += 1
            dominated = False
            for other in bucket:
                if include_diagnostics:
                    counters["dominance_checks"] += 1
                if _dominates(other, label) or other == label:
                    dominated = True
                    break
            if dominated:
                pruned += 1
                if include_diagnostics:
                    counters["labels_pruned"] += 1
                    timings["label_processing_ms"] += (perf_counter() - add_started) * 1000
                return
            survivors = []
            for other in bucket:
                if include_diagnostics:
                    counters["dominance_checks"] += 1
                if not _dominates(label, other):
                    survivors.append(other)
            pruned += len(bucket) - len(survivors)
            survivors.append(label)
            labels[key] = survivors
            if label.stop_id == destination_stop_id:
                candidate_cutoff = label.arrival + timedelta(
                    minutes=max_extra_minutes
                )
                arrival_cutoff = min(
                    arrival_cutoff or candidate_cutoff, candidate_cutoff
                )
            heapq.heappush(
                queue,
                (label.arrival, label.reliability_cost, label.transfers,
                 next(serial), label),
            )
            if include_diagnostics:
                counters["labels_pruned"] += len(bucket) - len(survivors)
                counters["labels_accepted"] += 1
                counters["queue_pushes"] += 1
                counters["maximum_queue_size"] = max(counters["maximum_queue_size"], len(queue))
                counters["maximum_labels_in_bucket"] = max(counters["maximum_labels_in_bucket"], len(survivors))
                timings["label_processing_ms"] += (perf_counter() - add_started) * 1000

        while queue:
            check_deadline()
            queue_started = perf_counter() if include_diagnostics else 0.0
            _, _, _, _, label = heapq.heappop(queue)
            if include_diagnostics:
                counters["queue_pops"] += 1
                timings["queue_processing_ms"] += (perf_counter() - queue_started) * 1000
            if arrival_cutoff is not None and label.arrival > arrival_cutoff:
                break
            if label not in labels.get((label.stop_id, label.current_trip_id), ()):
                if include_diagnostics:
                    counters["stale_labels_skipped"] += 1
                continue
            if label.stop_id == destination_stop_id:
                destinations.append(label)
                if include_diagnostics:
                    counters["destination_labels_found"] += 1
                if arrival_cutoff is None:
                    # The heap is ordered by scheduled arrival, so the first
                    # destination establishes the fastest feasible arrival.
                    arrival_cutoff = label.arrival + timedelta(
                        minutes=max_extra_minutes
                    )
                continue
            if include_diagnostics:
                counters["stops_expanded"] += 1
            transfer_started = perf_counter() if include_diagnostics else 0.0
            for transfer in transfers(label.stop_id):
                if include_diagnostics:
                    counters["transfer_rules_examined"] += 1
                target = transfer["to_stop_id"]
                if transfer["transfer_type"] == 3 or target == label.stop_id:
                    continue
                if target in label.visited:
                    continue
                required_trip = transfer.get("from_trip_id")
                if required_trip not in (None, label.current_trip_id):
                    continue
                arrival = label.arrival + timedelta(
                    seconds=transfer["min_transfer_time"] or 0
                )
                effective_horizon = min(
                    horizon, arrival_cutoff or timedelta.max
                )
                if arrival <= effective_horizon:
                    if include_diagnostics:
                        counters["walking_transfer_labels_created"] += 1
                    add(_Label(
                        target, arrival, label.reliability_cost, label.transfers,
                        None, label.rides, label.selections,
                        label.visited | {target},
                    ))
            if include_diagnostics:
                timings["transfer_expansion_ms"] += (perf_counter() - transfer_started) * 1000
            departure_started = perf_counter() if include_diagnostics else 0.0
            boardable: list[tuple[Connection, int, timedelta]] = []
            rules = transfers(label.stop_id)
            for first in departures(label.stop_id):
                if include_diagnostics:
                    counters["departures_examined"] += 1
                effective_horizon = min(
                    horizon, arrival_cutoff or timedelta.max
                )
                if (
                    first.departure_time < label.arrival
                    or first.departure_time > effective_horizon
                ):
                    continue
                # The original boarding scan already emitted labels for every
                # downstream alighting point on this vehicle.
                if first.trip_id == label.current_trip_id:
                    continue
                if active is None and not self.calendar.operates(
                    first.service_id, service_date
                ):
                    continue
                new_transfer = int(bool(label.rides) and first.trip_id != label.current_trip_id)
                if label.transfers + new_transfer > max_transfers:
                    continue
                matching = [
                    rule for rule in rules
                    if rule["to_stop_id"] == label.stop_id
                    and rule.get("from_trip_id") in (None, label.current_trip_id)
                    and rule.get("to_trip_id") in (None, first.trip_id)
                ]
                if any(rule["transfer_type"] == 3 for rule in matching):
                    continue
                minimum = max(
                    (rule["min_transfer_time"] or 0 for rule in matching), default=0
                )
                if first.trip_id != label.current_trip_id and first.departure_time < (
                    label.arrival + timedelta(seconds=minimum)
                ):
                    continue
                if include_diagnostics:
                    counters["boardable_departures"] += 1
                    counters["trips_examined"] += 1
                boardable.append((first, new_transfer, effective_horizon))
            if frontier_loader is not None and boardable:
                frontier_trip_ids = {item[0].trip_id for item in boardable}
                if include_diagnostics:
                    counters["candidate_trip_ids_from_frontier"] += len(
                        frontier_trip_ids
                    )
                loading_started = perf_counter()
                frontier_loader.ensure_loaded(frontier_trip_ids)
                if include_diagnostics:
                    timings["frontier_trip_loading_ms"] += (
                        perf_counter() - loading_started
                    ) * 1000
                check_deadline()
            for first, new_transfer, effective_horizon in boardable:
                segment: list[Connection] = []
                current = label.stop_id
                trip_started = perf_counter() if include_diagnostics else 0.0
                for connection in trip(first):
                    if include_diagnostics:
                        counters["connections_examined"] += 1
                    if connection.from_stop_id != current:
                        break
                    segment.append(connection)
                    current = connection.to_stop_id
                    if current in label.visited:
                        break
                    if connection.arrival_time > effective_horizon:
                        break
                    selection = profile(connection, current)
                    probability = (
                        selection.profile.reliability_probability
                        if selection.profile is not None else 0.0
                    )
                    add(_Label(
                        current,
                        connection.arrival_time,
                        label.reliability_cost
                        - math.log(max(probability, EPSILON)),
                        label.transfers + new_transfer,
                        first.trip_id,
                        label.rides + tuple(segment),
                        label.selections + (selection,),
                        label.visited | {current},
                    ))
                if include_diagnostics:
                    timings["trip_scanning_ms"] += (perf_counter() - trip_started) * 1000
            if include_diagnostics:
                timings["departure_scanning_ms"] += (perf_counter() - departure_started) * 1000

        searched_at = perf_counter()
        if include_diagnostics:
            timings["search_cpu_ms"] = (searched_at - loaded_at) * 1000
            if frontier_loader is not None:
                timings["frontier_trip_query_ms"] = frontier_loader.query_ms
                timings["frontier_trip_indexing_ms"] = frontier_loader.indexing_ms
                timings["search_cpu_excluding_frontier_io_ms"] = max(
                    0.0, timings["search_cpu_ms"] - frontier_loader.query_ms
                )
                counters["unique_frontier_trips_requested"] = len(
                    frontier_loader.loaded_trip_ids
                )
                counters["unique_frontier_trips_loaded"] = len(
                    frontier_loader.loaded_trip_ids - frontier_loader.known_empty_trip_ids
                )
                counters["frontier_connections_loaded"] = (
                    frontier_loader.connections_loaded
                )
                counters["frontier_trip_batch_query_count"] = (
                    frontier_loader.query_count
                )
                counters["frontier_trip_batch_sizes"] = tuple(
                    frontier_loader.batch_sizes
                )
                counters["average_frontier_trip_batch_size"] = (
                    sum(frontier_loader.batch_sizes) / len(frontier_loader.batch_sizes)
                    if frontier_loader.batch_sizes else 0.0
                )
                counters["maximum_frontier_trip_batch_size"] = max(
                    frontier_loader.batch_sizes, default=0
                )
                counters["single_trip_batch_count"] = sum(
                    size == 1 for size in frontier_loader.batch_sizes
                )
                counters["repeated_trip_fetch_attempts"] = (
                    frontier_loader.repeated_fetch_attempts
                )
                counters["known_empty_trip_count"] = len(
                    frontier_loader.known_empty_trip_ids
                )
                counters["eager_trips_avoided"] = max(
                    0, len(trip_ids) - len(frontier_loader.loaded_trip_ids)
                )
                caches["bulk_trip_query_count"] = frontier_loader.query_count
                caches["unique_trips_loaded"] = len(frontier_loader.loaded_trip_ids)
                caches["unique_connections_loaded"] = (
                    frontier_loader.connections_loaded
                )
                caches["request_index_memory_estimate_bytes"] += (
                    frontier_loader.memory_estimate_bytes
                )
            else:
                timings["search_cpu_excluding_frontier_io_ms"] = timings[
                    "search_cpu_ms"
                ]
        destination_started = perf_counter() if include_diagnostics else 0.0
        if destinations:
            fastest = min(item.arrival for item in destinations)
            cutoff = fastest + timedelta(minutes=max_extra_minutes)
            destinations = [item for item in destinations if item.arrival <= cutoff]
            destinations = [
                item for item in destinations
                if not any(
                    other is not item
                    and other.arrival <= item.arrival
                    and other.reliability_cost <= item.reliability_cost
                    and other.transfers <= item.transfers
                    and (
                        other.arrival < item.arrival
                        or other.reliability_cost < item.reliability_cost
                        or other.transfers < item.transfers
                    )
                    for other in destinations
                )
            ]
        if include_diagnostics:
            timings["destination_filtering_ms"] += (perf_counter() - destination_started) * 1000
        alternatives: list[ReliableAlternative] = []
        seen: set[tuple[tuple[str, str, str], ...]] = set()
        for label in sorted(
            destinations,
            key=lambda item: (
                item.reliability_cost, item.arrival, item.transfers,
                tuple((hop.trip_id, hop.from_stop_id, hop.to_stop_id) for hop in item.rides),
            ),
        ):
            reconstruction_started = perf_counter() if include_diagnostics else 0.0
            itinerary = self._reconstruct(
                origin, destination, service_date, departure_time, label,
                timings if include_diagnostics else None,
            )
            if include_diagnostics:
                timings["reconstruction_ms"] += (perf_counter() - reconstruction_started) * 1000
                counters["alternatives_reconstructed"] += 1
            identifier = itinerary_identity(itinerary)
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            alternatives.append(ReliableAlternative(
                itinerary,
                math.exp(-label.reliability_cost),
                label.reliability_cost,
                label.selections,
            ))
        ranked_at = perf_counter()
        returned = alternatives if limit is None else alternatives[:limit]
        if include_diagnostics:
            counters["alternatives_returned"] = len(returned)
        diagnostics = snapshot()
        if include_diagnostics:
            logger.info("Profiled reliable search completed", extra={
                "origin_stop_id": origin_stop_id,
                "destination_stop_id": destination_stop_id,
                "service_date": str(service_date),
                "departure_time": str(departure_time),
                "total_ms": (ranked_at - started) * 1000,
                "search_ms": (searched_at - loaded_at) * 1000,
                "timed_out": False,
                "diagnostics": diagnostics,
            })
        return ReliableSearchResult(
            tuple(returned),
            SearchTiming(
                (loaded_at - started) * 1000,
                (searched_at - loaded_at) * 1000,
                (ranked_at - searched_at) * 1000,
                (ranked_at - started) * 1000,
            ),
            pruned,
            diagnostics,
        )

    def _reconstruct(
        self, origin: Stop, destination: Stop, service_date: date,
        departure: timedelta, label: _Label,
        diagnostic_timings: dict[str, float] | None = None,
    ) -> Itinerary:
        legs: list[RouteLeg] = []
        query_started = perf_counter() if diagnostic_timings is not None else 0.0
        stops_by_id = load_connection_stops(self.database, label.rides)
        if diagnostic_timings is not None:
            diagnostic_timings["reconstruction_queries_ms"] += (
                perf_counter() - query_started
            ) * 1000
        index = 0
        while index < len(label.rides):
            first = label.rides[index]
            last = first
            leg_rides = [first]
            index += 1
            while index < len(label.rides) and label.rides[index].trip_id == first.trip_id:
                last = label.rides[index]
                leg_rides.append(last)
                index += 1
            leg_origin = stops_by_id.get(first.from_stop_id)
            leg_destination = stops_by_id.get(last.to_stop_id)
            if leg_origin is None or leg_destination is None:
                raise RuntimeError("a routed stop disappeared during reconstruction")
            legs.append(RouteLeg(
                first.trip_id, first.route_id, first.route_name,
                leg_origin, leg_destination, first.departure_time, last.arrival_time,
                first.direction_id, build_leg_stops(leg_rides, stops_by_id),
            ))
        return Itinerary(
            origin, destination, service_date, departure, label.arrival, tuple(legs)
        )
