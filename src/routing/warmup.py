"""Version-aware startup warming for the existing routing cache manager."""

from __future__ import annotations

import logging
import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import date, timedelta
from threading import Event, RLock
from time import perf_counter
from sys import getsizeof
from types import MappingProxyType
from typing import Any

from .cache import (
    RoutingCacheManager, _enabled, _nonnegative_int, _positive_float,
    _positive_int,
)
from .models import Connection
from .search_data import RequestTripConnectionLoader
from .service_date import current_service_date

logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class WarmupConfiguration:
    enabled: bool = True
    block_readiness: bool = False
    today_index: bool = False
    skytrain: bool = False
    tomorrow_index: bool = False
    skytrain_max_trips: int = 100
    skytrain_start_minutes: int = 0
    skytrain_horizon_minutes: int = 120
    skytrain_memory_budget_mb: int = 24
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "WarmupConfiguration":
        return cls(
            enabled=_enabled("CACHE_WARMUP_ENABLED", True),
            block_readiness=_enabled("CACHE_WARMUP_BLOCK_READINESS", False),
            today_index=_enabled("CACHE_WARMUP_TODAY_INDEX", False),
            skytrain=_enabled("CACHE_WARMUP_SKYTRAIN", False),
            tomorrow_index=_enabled("CACHE_WARMUP_TOMORROW_INDEX", False),
            skytrain_max_trips=_positive_int("CACHE_WARMUP_SKYTRAIN_MAX_TRIPS", 100),
            skytrain_start_minutes=_nonnegative_int("CACHE_WARMUP_SKYTRAIN_START_MINUTES", 0),
            skytrain_horizon_minutes=_positive_int("CACHE_WARMUP_SKYTRAIN_HORIZON_MINUTES", 120),
            skytrain_memory_budget_mb=_positive_int("CACHE_WARMUP_SKYTRAIN_MEMORY_BUDGET_MB", 24),
            timeout_seconds=_positive_float("CACHE_WARMUP_TIMEOUT_SECONDS", 30),
        )


@dataclass(frozen=True)
class WarmupState:
    ready: bool = False
    gtfs_version: str | None = None
    warmup_started: bool = False
    warmup_complete: bool = False
    warmup_failed: bool = False
    essential_warmup_complete: bool = False
    skytrain_warmup_complete: bool = False
    background_warmup_running: bool = False
    warmup_total_ms: float = 0.0
    static_snapshot_warmup_ms: float = 0.0
    daily_index_warmup_ms: float = 0.0
    skytrain_warmup_ms: float = 0.0
    reliability_warmup_ms: float = 0.0
    skytrain_routes_found: int = 0
    skytrain_active_trips: int = 0
    skytrain_connections_loaded: int = 0
    skytrain_cache_entries: int = 0
    skytrain_cache_memory_bytes: int = 0
    failure_phase: str | None = None


def ensure_static_snapshot(
    cache: RoutingCacheManager, database: Any, gtfs_version: str
) -> tuple[object, float]:
    found, value = cache.static_snapshots.get(gtfs_version)
    if found:
        return value, 0.0

    def build():
        found_again, cached = cache.static_snapshots.get(gtfs_version)
        if found_again:
            return cached, 0.0
        started = perf_counter()
        value = (
            MappingProxyType(dict(database.all_stop_coordinates())),
            tuple(
                MappingProxyType(dict(item))
                for item in database.bulk_transfers()
            ),
        )
        cache.static_snapshots.put(gtfs_version, value)
        return value, (perf_counter() - started) * 1000

    return cache.single_flight("static", gtfs_version, build)


@dataclass(frozen=True)
class StopDepartureIndex:
    values: tuple[Connection, ...]
    times: tuple[timedelta, ...]
    memory_estimate_bytes: int

    def window(self, earliest: timedelta, latest: timedelta) -> tuple[Connection, ...]:
        return self.values[bisect_left(self.times, earliest):bisect_right(self.times, latest)]


def ensure_stop_departures(
    cache: RoutingCacheManager, database: Any, gtfs_version: str,
    service_date: date, stop_id: str, service_ids: set[str] | None,
) -> tuple[StopDepartureIndex, float]:
    key = (gtfs_version, service_date, stop_id)
    found, value = cache.departures.get(key)
    if found:
        return value, 0.0

    def build():
        found_again, cached = cache.departures.get(key)
        if found_again:
            return cached, 0.0
        started = perf_counter()
        loader = getattr(database, "daily_departures_from_stop", None)
        if callable(loader):
            rows = loader(stop_id, service_ids=service_ids)
        else:
            rows, offset = [], 0
            while True:
                batch = database.departures_from(
                    stop_id, timedelta(0), limit=256, offset=offset,
                    service_ids=service_ids,
                )
                rows.extend(batch)
                if len(batch) < 256:
                    break
                offset += 256
        ordered = tuple(sorted(rows, key=lambda item: (
            item.departure_time, item.trip_id, item.from_stop_sequence
        )))
        value = StopDepartureIndex(
            ordered, tuple(item.departure_time for item in ordered),
            getsizeof(ordered) + sum(getsizeof(item) for item in ordered),
        )
        cache.departures.put(key, value)
        return value, (perf_counter() - started) * 1000

    return cache.single_flight("departure", key, build)


class RoutingWarmupCoordinator:
    def __init__(
        self, cache: RoutingCacheManager, transit_database: Any,
        reliability_database: Any, configuration: WarmupConfiguration | None = None,
    ) -> None:
        self.cache = cache
        self.transit_database = transit_database
        self.reliability_database = reliability_database
        self.configuration = configuration or WarmupConfiguration.from_environment()
        # Optional cache construction never gates API readiness.
        self._state = WarmupState(ready=True)
        self._state_lock = RLock()
        self._stop = Event()

    def request_stop(self) -> None:
        self._stop.set()

    def state(self) -> WarmupState:
        with self._state_lock:
            return self._state

    def _update(self, **changes: Any) -> None:
        with self._state_lock:
            self._state = replace(self._state, **changes)

    def warm_essential(self, service_date: date | None = None) -> WarmupState:
        if not self.configuration.enabled:
            self._update(ready=True, warmup_complete=True, essential_warmup_complete=True)
            return self.state()
        service_date = service_date or current_service_date()
        started = perf_counter()
        self._update(warmup_started=True, warmup_failed=False, failure_phase=None)
        phase = "gtfs_version"
        try:
            version = self.transit_database.gtfs_version()
            self._update(gtfs_version=version)
            phase = "static_snapshot"
            _, static_ms = ensure_static_snapshot(self.cache, self.transit_database, version)
            daily_ms = 0.0
            # The legacy today-index flag is accepted but intentionally does
            # not recreate the removed network-wide daily index.
            self._update(
                ready=True,
                essential_warmup_complete=True,
                static_snapshot_warmup_ms=static_ms,
                daily_index_warmup_ms=daily_ms,
            )
            if self.configuration.skytrain and not self._stop.is_set():
                phase = "skytrain"
                self.warm_skytrain(version, service_date)
            self._update(
                warmup_complete=True,
                warmup_total_ms=(perf_counter() - started) * 1000,
            )
        except Exception:
            logger.exception("Routing cache warm-up failed during %s", phase)
            self._update(
                ready=True,
                warmup_failed=True,
                failure_phase=phase,
                warmup_total_ms=(perf_counter() - started) * 1000,
            )
            raise
        return self.state()

    def warm_skytrain(self, gtfs_version: str, service_date: date) -> None:
        key = (gtfs_version, service_date)
        marker_key = ("skytrain", gtfs_version, service_date)
        if self.cache.warmups.get(marker_key)[0]:
            self._update(skytrain_warmup_complete=True)
            return

        def build():
            if self.cache.warmups.get(marker_key)[0]:
                self._update(skytrain_warmup_complete=True)
                return
            started = perf_counter()
            service_found, services = self.cache.service_days.get(key)
            if not service_found:
                services = frozenset(self.transit_database.active_service_ids(service_date))
                self.cache.service_days.put(key, services)
            window_loader = getattr(
                self.transit_database, "active_rail_trip_ids_in_window", None
            )
            if callable(window_loader):
                earliest = timedelta(minutes=self.configuration.skytrain_start_minutes)
                route_count, trip_ids = window_loader(
                    set(services or ()), earliest,
                    earliest + timedelta(minutes=self.configuration.skytrain_horizon_minutes),
                )
            else:
                route_count, trip_ids = self.transit_database.active_rail_trip_ids(
                    set(services or ())
                )
            trip_ids = set(sorted(trip_ids)[:self.configuration.skytrain_max_trips])
            before = self.cache.memory_estimate_bytes()
            loader = RequestTripConnectionLoader(
                self.transit_database, shared_cache=self.cache,
                gtfs_version=gtfs_version,
            )
            loader.ensure_loaded(trip_ids)
            memory_used = max(0, self.cache.memory_estimate_bytes() - before)
            if memory_used > self.configuration.skytrain_memory_budget_mb * 1024 * 1024:
                for trip_id in trip_ids:
                    self.cache.trips.remove_where(
                        lambda key, trip_id=trip_id: key == (gtfs_version, trip_id)
                    )
                raise MemoryError("SkyTrain warm-up exceeded its cache memory budget")
            self._update(
                skytrain_warmup_complete=True,
                skytrain_warmup_ms=(perf_counter() - started) * 1000,
                skytrain_routes_found=route_count,
                skytrain_active_trips=len(trip_ids),
                skytrain_connections_loaded=loader.connections_loaded,
                skytrain_cache_entries=len(loader.loaded_trip_ids),
                skytrain_cache_memory_bytes=memory_used,
            )
            self.cache.warmups.put(marker_key, True)

        self.cache.single_flight("skytrain", key, build)

    def warm_tomorrow(self) -> None:
        state = self.state()
        if not state.gtfs_version or self._stop.is_set():
            return
        self._update(background_warmup_running=True)
        try:
            tomorrow = current_service_date() + timedelta(days=1)
            if self._stop.is_set():
                return
            # Departure entries are loaded only for stops reached by a search.
            if self.configuration.skytrain and not self._stop.is_set():
                self.warm_skytrain(state.gtfs_version, tomorrow)
        except Exception:
            logger.exception("Background routing cache warm-up failed")
        finally:
            self._update(background_warmup_running=False)
