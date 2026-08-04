"""Request-local, immutable-by-convention indexes for reliable search."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from sys import getsizeof
from time import perf_counter
from types import MappingProxyType
from typing import Any

from .cache import RoutingCacheManager
from .models import Connection

DEFAULT_FRONTIER_TRIP_BATCH_SIZE = 256


class RequestTripConnectionLoader:
    """Request-local, deduplicating frontier loader for complete trip chains."""

    def __init__(
        self,
        repository: Any,
        *,
        batch_size: int = DEFAULT_FRONTIER_TRIP_BATCH_SIZE,
        shared_cache: RoutingCacheManager | None = None,
        gtfs_version: str = "unknown",
    ):
        self.repository = repository
        self.batch_size = max(1, batch_size)
        self._connections: dict[str, tuple[Connection, ...]] = {}
        self.shared_cache = shared_cache
        self.gtfs_version = gtfs_version
        self.loaded_trip_ids: set[str] = set()
        self.known_empty_trip_ids: set[str] = set()
        self.pending_trip_ids: set[str] = set()
        self.failed_trip_ids: set[str] = set()
        self.batch_sizes: list[int] = []
        self.query_count = 0
        self.repeated_fetch_attempts = 0
        self.request_cache_hits = 0
        self.shared_cache_hits = 0
        self.shared_cache_misses = 0
        self.negative_cache_hits = 0
        self.connections_loaded = 0
        self.query_ms = 0.0
        self.indexing_ms = 0.0

    def ensure_loaded(self, trip_ids: Collection[str]) -> None:
        requested = set(trip_ids)
        self.request_cache_hits += len(requested & self.loaded_trip_ids)
        missing = sorted(requested - self.loaded_trip_ids - self.failed_trip_ids)
        if self.shared_cache is not None:
            database_missing: list[str] = []
            for trip_id in missing:
                before = self.shared_cache.trips.statistics().negative_hits
                found, cached = self.shared_cache.trips.get(
                    (self.gtfs_version, trip_id)
                )
                if found:
                    ordered = tuple(cached or ())
                    self._connections[trip_id] = ordered
                    self.loaded_trip_ids.add(trip_id)
                    self.shared_cache_hits += 1
                    if not ordered:
                        self.known_empty_trip_ids.add(trip_id)
                        self.negative_cache_hits += (
                            self.shared_cache.trips.statistics().negative_hits - before
                        )
                else:
                    self.shared_cache_misses += 1
                    database_missing.append(trip_id)
            missing = database_missing
        self.pending_trip_ids.update(missing)
        while missing:
            batch = missing[: self.batch_size]
            missing = missing[self.batch_size :]
            query_started = perf_counter()
            try:
                rows = self.repository.bulk_trip_connections(
                    set(batch), batch_size=self.batch_size
                )
            except Exception:
                self.failed_trip_ids.update(batch)
                self.pending_trip_ids.difference_update(batch)
                raise
            self.query_ms += (perf_counter() - query_started) * 1000
            self.query_count += 1
            self.batch_sizes.append(len(batch))
            indexing_started = perf_counter()
            grouped: dict[str, list[Connection]] = defaultdict(list)
            for row in rows:
                grouped[row.trip_id].append(row)
            for trip_id in batch:
                ordered = tuple(
                    sorted(
                        grouped.get(trip_id, ()),
                        key=lambda item: item.from_stop_sequence,
                    )
                )
                self._connections[trip_id] = ordered
                if self.shared_cache is not None:
                    self.shared_cache.trips.put(
                        (self.gtfs_version, trip_id),
                        ordered,
                        ttl_seconds=(
                            self.shared_cache.configuration.negative_trip_ttl_seconds
                            if not ordered
                            else None
                        ),
                        negative=not ordered,
                    )
                self.loaded_trip_ids.add(trip_id)
                if not ordered:
                    self.known_empty_trip_ids.add(trip_id)
            self.connections_loaded += len(rows)
            self.pending_trip_ids.difference_update(batch)
            self.indexing_ms += (perf_counter() - indexing_started) * 1000

    def connections_for(self, trip_id: str) -> Sequence[Connection]:
        return self._connections.get(trip_id, ())

    def is_loaded(self, trip_id: str) -> bool:
        return trip_id in self.loaded_trip_ids

    @property
    def memory_estimate_bytes(self) -> int:
        return (
            getsizeof(self._connections)
            + sum(getsizeof(items) for items in self._connections.values())
            + sum(
                getsizeof(item)
                for items in self._connections.values()
                for item in items
            )
            + getsizeof(self.loaded_trip_ids)
            + getsizeof(self.known_empty_trip_ids)
        )


@dataclass(frozen=True)
class SearchDataIndex:
    departures_by_stop: Mapping[str, tuple[Connection, ...]]
    departure_times_by_stop: Mapping[str, tuple[timedelta, ...]]
    connections_by_trip: Mapping[str, tuple[Connection, ...]]
    connection_sequences_by_trip: Mapping[str, tuple[int, ...]]
    transfers_by_stop: Mapping[str, tuple[Mapping[str, Any], ...]]
    memory_estimate_bytes: int

    @classmethod
    def build(
        cls,
        departures: list[Connection],
        connections: list[Connection],
        transfers: list[dict[str, Any]],
    ) -> SearchDataIndex:
        return cls.build_profiled(departures, connections, transfers)[0]

    @classmethod
    def build_profiled(
        cls,
        departures: list[Connection],
        connections: list[Connection],
        transfers: list[dict[str, Any]],
    ) -> tuple[SearchDataIndex, dict[str, float]]:
        grouping_started = perf_counter()
        departure_groups: dict[str, list[Connection]] = defaultdict(list)
        for item in departures:
            departure_groups[item.from_stop_id].append(item)
        trip_groups: dict[str, list[Connection]] = defaultdict(list)
        for item in connections:
            trip_groups[item.trip_id].append(item)
        transfer_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in transfers:
            transfer_groups[item["from_stop_id"]].append(item)
        grouping_ms = (perf_counter() - grouping_started) * 1000

        sorting_started = perf_counter()
        departure_index = {
            stop_id: tuple(
                sorted(
                    items,
                    key=lambda item: (
                        item.departure_time,
                        item.trip_id,
                        item.from_stop_sequence,
                    ),
                )
            )
            for stop_id, items in departure_groups.items()
        }

        trip_index = {
            trip_id: tuple(sorted(items, key=lambda item: item.from_stop_sequence))
            for trip_id, items in trip_groups.items()
        }

        transfer_index = {
            stop_id: tuple(MappingProxyType(dict(item)) for item in items)
            for stop_id, items in transfer_groups.items()
        }
        sorting_ms = (perf_counter() - sorting_started) * 1000
        estimate = (
            getsizeof(departures)
            + getsizeof(connections)
            + getsizeof(transfers)
            + sum(getsizeof(item) for item in departures)
            + sum(getsizeof(item) for item in connections)
            + sum(getsizeof(item) for item in transfers)
        )
        index = cls(
            MappingProxyType(departure_index),
            MappingProxyType(
                {
                    stop_id: tuple(item.departure_time for item in items)
                    for stop_id, items in departure_index.items()
                }
            ),
            MappingProxyType(trip_index),
            MappingProxyType(
                {
                    trip_id: tuple(item.from_stop_sequence for item in items)
                    for trip_id, items in trip_index.items()
                }
            ),
            MappingProxyType(transfer_index),
            estimate,
        )
        return index, {"grouping_ms": grouping_ms, "sorting_ms": sorting_ms}

    def departures(
        self, stop_id: str, earliest: timedelta, latest: timedelta
    ) -> tuple[Connection, ...]:
        items = self.departures_by_stop.get(stop_id, ())
        times = self.departure_times_by_stop.get(stop_id, ())
        return items[bisect_left(times, earliest) : bisect_right(times, latest)]

    def trip_connections(
        self, trip_id: str, from_stop_sequence: int
    ) -> tuple[Connection, ...]:
        items = self.connections_by_trip.get(trip_id, ())
        sequences = self.connection_sequences_by_trip.get(trip_id, ())
        return items[bisect_left(sequences, from_stop_sequence) :]

    def transfers(self, stop_id: str) -> tuple[Mapping[str, Any], ...]:
        return self.transfers_by_stop.get(stop_id, ())
