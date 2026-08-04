"""Application-scoped, bounded caches for immutable routing data.

The caches deliberately contain version identifiers in every externally visible
key.  Database work is performed by callers, outside the cache lock; the small
single-flight helper only coordinates publication of completed values.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from sys import getsizeof
from threading import Event, RLock
from time import monotonic

# Change this one value to switch the application's default caching path.
# Individual API requests and benchmark runs can still override it.
DEFAULT_ROUTING_CACHE_MODE = "request"  # "shared" or "request"


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _positive_float(name: str, default: float) -> float:
    try:
        return max(0.001, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return (
        default
        if value is None
        else value.strip().lower() in {"1", "true", "yes", "on"}
    )


@dataclass(frozen=True)
class CacheConfiguration:
    trip_capacity: int = 10_000
    trip_ttl_seconds: float = 3600.0
    negative_trip_ttl_seconds: float = 60.0
    daily_index_capacity: int = 3
    heuristic_capacity: int = 50_000
    heuristic_ttl_seconds: float = 3600.0
    profile_capacity: int = 20_000
    profile_ttl_seconds: float = 300.0
    response_capacity: int = 256
    response_ttl_seconds: float = 60.0
    response_enabled: bool = True

    @classmethod
    def from_environment(cls) -> CacheConfiguration:
        return cls(
            trip_capacity=_positive_int("ROUTING_TRIP_CACHE_CAPACITY", 10_000),
            trip_ttl_seconds=_positive_float("ROUTING_TRIP_CACHE_TTL_SECONDS", 3600),
            negative_trip_ttl_seconds=_positive_float(
                "ROUTING_NEGATIVE_TRIP_CACHE_TTL_SECONDS", 60
            ),
            daily_index_capacity=_positive_int("ROUTING_DAILY_INDEX_CAPACITY", 3),
            heuristic_capacity=_positive_int(
                "ROUTING_HEURISTIC_CACHE_CAPACITY", 50_000
            ),
            heuristic_ttl_seconds=_positive_float(
                "ROUTING_HEURISTIC_CACHE_TTL_SECONDS", 3600
            ),
            profile_capacity=_positive_int("ROUTING_PROFILE_CACHE_CAPACITY", 20_000),
            profile_ttl_seconds=_positive_float(
                "ROUTING_PROFILE_CACHE_TTL_SECONDS", 300
            ),
            response_capacity=_positive_int("ROUTING_RESPONSE_CACHE_CAPACITY", 256),
            response_ttl_seconds=_positive_float(
                "ROUTING_RESPONSE_CACHE_TTL_SECONDS", 60
            ),
            response_enabled=_enabled("ROUTING_RESPONSE_CACHE_ENABLED", True),
        )


@dataclass(frozen=True)
class CacheStatistics:
    hits: int
    misses: int
    evictions: int
    loads: int
    negative_hits: int
    entries: int


@dataclass
class _Entry[V]:
    value: V
    expires_at: float
    negative: bool = False


class BoundedTTLCache[K: Hashable, V]:
    """A small thread-safe LRU/TTL cache with immutable statistics snapshots."""

    def __init__(self, capacity: int, ttl_seconds: float) -> None:
        self.capacity = max(1, capacity)
        self.ttl_seconds = max(0.001, ttl_seconds)
        self._items: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._lock = RLock()
        self._hits = self._misses = self._evictions = self._loads = 0
        self._negative_hits = 0

    def get(self, key: K) -> tuple[bool, V | None]:
        now = monotonic()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self._misses += 1
                return False, None
            if entry.expires_at <= now:
                del self._items[key]
                self._misses += 1
                return False, None
            self._items.move_to_end(key)
            self._hits += 1
            if entry.negative:
                self._negative_hits += 1
            return True, entry.value

    def put(
        self,
        key: K,
        value: V,
        *,
        ttl_seconds: float | None = None,
        negative: bool = False,
    ) -> None:
        expires = monotonic() + (
            self.ttl_seconds if ttl_seconds is None else max(0.001, ttl_seconds)
        )
        with self._lock:
            self._items[key] = _Entry(value, expires, negative)
            self._items.move_to_end(key)
            self._loads += 1
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._hits = self._misses = self._evictions = self._loads = 0
            self._negative_hits = 0

    def remove_where(self, predicate: Callable[[K], bool]) -> int:
        with self._lock:
            keys = [key for key in self._items if predicate(key)]
            for key in keys:
                del self._items[key]
            return len(keys)

    def statistics(self) -> CacheStatistics:
        with self._lock:
            return CacheStatistics(
                self._hits,
                self._misses,
                self._evictions,
                self._loads,
                self._negative_hits,
                len(self._items),
            )

    def memory_estimate_bytes(self) -> int:
        with self._lock:
            total = getsizeof(self._items)
            for key, entry in self._items.items():
                total += getsizeof(key) + getsizeof(entry) + getsizeof(entry.value)
                total += int(getattr(entry.value, "memory_estimate_bytes", 0))
            return total


class RoutingCacheManager:
    """Own all process-wide routing caches for one FastAPI application."""

    def __init__(self, configuration: CacheConfiguration | None = None) -> None:
        self.configuration = configuration or CacheConfiguration.from_environment()
        c = self.configuration
        self.trips: BoundedTTLCache[tuple[str, str], tuple[object, ...]] = (
            BoundedTTLCache(c.trip_capacity, c.trip_ttl_seconds)
        )
        self.daily_indexes: BoundedTTLCache[tuple[str, object], object] = (
            BoundedTTLCache(c.daily_index_capacity, c.trip_ttl_seconds)
        )
        self.service_days: BoundedTTLCache[tuple[str, object], frozenset[str]] = (
            BoundedTTLCache(c.daily_index_capacity, c.trip_ttl_seconds)
        )
        self.static_snapshots: BoundedTTLCache[str, object] = BoundedTTLCache(
            2, c.trip_ttl_seconds
        )
        self.profiles: BoundedTTLCache[tuple[str, str, int | None, str], object] = (
            BoundedTTLCache(c.profile_capacity, c.profile_ttl_seconds)
        )
        self.heuristics: BoundedTTLCache[tuple[str, str, str], float] = BoundedTTLCache(
            c.heuristic_capacity, c.heuristic_ttl_seconds
        )
        self.responses: BoundedTTLCache[tuple[object, ...], object] = BoundedTTLCache(
            c.response_capacity, c.response_ttl_seconds
        )
        self.warmups: BoundedTTLCache[tuple[str, str, object], bool] = BoundedTTLCache(
            max(4, c.daily_index_capacity * 2), c.trip_ttl_seconds
        )
        self._flights: dict[tuple[str, Hashable], Event] = {}
        self._flight_lock = RLock()
        self._daily_build_ms = 0.0
        self._daily_entry_count = 0
        self._single_flight_wait_count = 0
        self._search_count = 0

    def register_search(self) -> bool:
        with self._flight_lock:
            self._search_count += 1
            return self._search_count == 1

    def record_daily_build(self, milliseconds: float, entry_count: int) -> None:
        with self._flight_lock:
            self._daily_build_ms += max(0.0, milliseconds)
            self._daily_entry_count = max(0, entry_count)

    def daily_build_statistics(self) -> tuple[float, int]:
        with self._flight_lock:
            return self._daily_build_ms, self._daily_entry_count

    def single_flight[V](
        self, namespace: str, key: Hashable, loader: Callable[[], V]
    ) -> V:
        """Run one loader per key and publish only its completed return value."""
        flight_key = (namespace, key)
        while True:
            with self._flight_lock:
                event = self._flights.get(flight_key)
                if event is None:
                    event = Event()
                    self._flights[flight_key] = event
                    owner = True
                else:
                    owner = False
            if owner:
                try:
                    return loader()
                finally:
                    with self._flight_lock:
                        self._flights.pop(flight_key, None)
                        event.set()
            with self._flight_lock:
                self._single_flight_wait_count += 1
            event.wait()

    @property
    def single_flight_wait_count(self) -> int:
        with self._flight_lock:
            return self._single_flight_wait_count

    def clear(self) -> None:
        for cache in (
            self.trips,
            self.daily_indexes,
            self.service_days,
            self.static_snapshots,
            self.profiles,
            self.heuristics,
            self.responses,
            self.warmups,
        ):
            cache.clear()
        with self._flight_lock:
            self._daily_build_ms = 0.0
            self._daily_entry_count = 0
            self._single_flight_wait_count = 0
            self._search_count = 0

    def invalidate_gtfs_version(self, version: str) -> int:
        """Explicitly remove one feed version; versioned lookup is safe without it."""
        return sum(
            (
                self.trips.remove_where(lambda key: key[0] == version),
                self.daily_indexes.remove_where(lambda key: key[0] == version),
                self.service_days.remove_where(lambda key: key[0] == version),
                self.static_snapshots.remove_where(lambda key: key == version),
                self.heuristics.remove_where(lambda key: key[0] == version),
                self.responses.remove_where(
                    lambda key: bool(key) and key[0] == version
                ),
                self.warmups.remove_where(lambda key: key[1] == version),
            )
        )

    def invalidate_profile_version(self, version: str) -> int:
        return sum(
            (
                self.profiles.remove_where(lambda key: key[0] == version),
                self.responses.remove_where(
                    lambda key: len(key) > 1 and key[1] == version
                ),
            )
        )

    def statistics(self) -> dict[str, CacheStatistics]:
        return {
            "trips": self.trips.statistics(),
            "daily_indexes": self.daily_indexes.statistics(),
            "service_days": self.service_days.statistics(),
            "static_snapshots": self.static_snapshots.statistics(),
            "profiles": self.profiles.statistics(),
            "heuristics": self.heuristics.statistics(),
            "responses": self.responses.statistics(),
            "warmups": self.warmups.statistics(),
        }

    def memory_estimate_bytes(self) -> int:
        return sum(
            cache.memory_estimate_bytes()
            for cache in (
                self.trips,
                self.daily_indexes,
                self.service_days,
                self.static_snapshots,
                self.profiles,
                self.heuristics,
                self.responses,
                self.warmups,
            )
        )
