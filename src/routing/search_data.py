"""Request-local, immutable-by-convention indexes for reliable search."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from sys import getsizeof
from typing import Any
from collections.abc import Collection, Sequence
from time import perf_counter

from .models import Connection

DEFAULT_FRONTIER_TRIP_BATCH_SIZE = 256


class RequestTripConnectionLoader:
    """Request-local, deduplicating frontier loader for complete trip chains."""

    def __init__(self, repository: Any, *, batch_size: int = DEFAULT_FRONTIER_TRIP_BATCH_SIZE):
        self.repository = repository
        self.batch_size = max(1, batch_size)
        self._connections: dict[str, tuple[Connection, ...]] = {}
        self.loaded_trip_ids: set[str] = set()
        self.known_empty_trip_ids: set[str] = set()
        self.pending_trip_ids: set[str] = set()
        self.failed_trip_ids: set[str] = set()
        self.batch_sizes: list[int] = []
        self.query_count = 0
        self.repeated_fetch_attempts = 0
        self.connections_loaded = 0
        self.query_ms = 0.0
        self.indexing_ms = 0.0

    def ensure_loaded(self, trip_ids: Collection[str]) -> None:
        requested = set(trip_ids)
        missing = sorted(requested - self.loaded_trip_ids - self.failed_trip_ids)
        self.pending_trip_ids.update(missing)
        while missing:
            batch = missing[:self.batch_size]
            missing = missing[self.batch_size:]
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
                ordered = tuple(sorted(
                    grouped.get(trip_id, ()),
                    key=lambda item: item.from_stop_sequence,
                ))
                self._connections[trip_id] = ordered
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
    departures_by_stop: dict[str, tuple[Connection, ...]]
    departure_times_by_stop: dict[str, tuple[timedelta, ...]]
    connections_by_trip: dict[str, tuple[Connection, ...]]
    connection_sequences_by_trip: dict[str, tuple[int, ...]]
    transfers_by_stop: dict[str, tuple[dict[str, Any], ...]]
    memory_estimate_bytes: int

    @classmethod
    def build(
        cls,
        departures: list[Connection],
        connections: list[Connection],
        transfers: list[dict[str, Any]],
    ) -> "SearchDataIndex":
        departure_groups: dict[str, list[Connection]] = defaultdict(list)
        for item in departures:
            departure_groups[item.from_stop_id].append(item)
        departure_index = {
            stop_id: tuple(sorted(items, key=lambda item: (
                item.departure_time, item.trip_id, item.from_stop_sequence
            )))
            for stop_id, items in departure_groups.items()
        }

        trip_groups: dict[str, list[Connection]] = defaultdict(list)
        for item in connections:
            trip_groups[item.trip_id].append(item)
        trip_index = {
            trip_id: tuple(sorted(items, key=lambda item: item.from_stop_sequence))
            for trip_id, items in trip_groups.items()
        }

        transfer_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in transfers:
            transfer_groups[item["from_stop_id"]].append(item)
        transfer_index = {
            stop_id: tuple(items) for stop_id, items in transfer_groups.items()
        }
        estimate = (
            getsizeof(departures) + getsizeof(connections) + getsizeof(transfers)
            + sum(getsizeof(item) for item in departures)
            + sum(getsizeof(item) for item in connections)
            + sum(getsizeof(item) for item in transfers)
        )
        return cls(
            departure_index,
            {
                stop_id: tuple(item.departure_time for item in items)
                for stop_id, items in departure_index.items()
            },
            trip_index,
            {
                trip_id: tuple(item.from_stop_sequence for item in items)
                for trip_id, items in trip_index.items()
            },
            transfer_index,
            estimate,
        )

    def departures(
        self, stop_id: str, earliest: timedelta, latest: timedelta
    ) -> tuple[Connection, ...]:
        items = self.departures_by_stop.get(stop_id, ())
        times = self.departure_times_by_stop.get(stop_id, ())
        return items[bisect_left(times, earliest):bisect_right(times, latest)]

    def trip_connections(
        self, trip_id: str, from_stop_sequence: int
    ) -> tuple[Connection, ...]:
        items = self.connections_by_trip.get(trip_id, ())
        sequences = self.connection_sequences_by_trip.get(trip_id, ())
        return items[bisect_left(sequences, from_stop_sequence):]

    def transfers(self, stop_id: str) -> tuple[dict[str, Any], ...]:
        return self.transfers_by_stop.get(stop_id, ())
