"""Version-aware startup warming for the existing routing cache manager."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from threading import Event, RLock
from time import perf_counter
from types import MappingProxyType
from typing import Any

from .cache import RoutingCacheManager, _enabled, _positive_float
from .search_data import RequestTripConnectionLoader, SearchDataIndex

logger = logging.getLogger(__name__)
VANCOUVER_TIMEZONE = ZoneInfo("America/Vancouver")


def current_service_date() -> date:
    return datetime.now(VANCOUVER_TIMEZONE).date()


@dataclass(frozen=True)
class WarmupConfiguration:
    enabled: bool = True
    block_readiness: bool = True
    today_index: bool = True
    skytrain: bool = True
    tomorrow_index: bool = False
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "WarmupConfiguration":
        return cls(
            enabled=_enabled("CACHE_WARMUP_ENABLED", True),
            block_readiness=_enabled("CACHE_WARMUP_BLOCK_READINESS", True),
            today_index=_enabled("CACHE_WARMUP_TODAY_INDEX", True),
            skytrain=_enabled("CACHE_WARMUP_SKYTRAIN", True),
            tomorrow_index=_enabled("CACHE_WARMUP_TOMORROW_INDEX", False),
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


def ensure_daily_index(
    cache: RoutingCacheManager, database: Any, gtfs_version: str,
    service_date: date,
) -> tuple[SearchDataIndex, dict[str, float]]:
    key = (gtfs_version, service_date)
    found, value = cache.daily_indexes.get(key)
    if found:
        return value, {"total_ms": 0.0, "query_ms": 0.0, "grouping_ms": 0.0, "sorting_ms": 0.0}

    def build():
        found_again, cached = cache.daily_indexes.get(key)
        if found_again:
            return cached, {"total_ms": 0.0, "query_ms": 0.0, "grouping_ms": 0.0, "sorting_ms": 0.0}
        started = perf_counter()
        service_found, services = cache.service_days.get(key)
        if not service_found:
            services = frozenset(database.active_service_ids(service_date))
            cache.service_days.put(key, services)
        static, _ = ensure_static_snapshot(cache, database, gtfs_version)
        query_started = perf_counter()
        departures = database.bulk_daily_departures(service_ids=set(services or ()))
        query_ms = (perf_counter() - query_started) * 1000
        index, index_timings = SearchDataIndex.build_profiled(
            departures, [], list(static[1])
        )
        cache.daily_indexes.put(key, index)
        total_ms = (perf_counter() - started) * 1000
        cache.record_daily_build(total_ms, len(departures))
        return index, {
            "total_ms": total_ms,
            "query_ms": query_ms,
            **index_timings,
        }

    return cache.single_flight("daily", key, build)


class RoutingWarmupCoordinator:
    def __init__(
        self, cache: RoutingCacheManager, transit_database: Any,
        reliability_database: Any, configuration: WarmupConfiguration | None = None,
    ) -> None:
        self.cache = cache
        self.transit_database = transit_database
        self.reliability_database = reliability_database
        self.configuration = configuration or WarmupConfiguration.from_environment()
        self._state = WarmupState(ready=not self.configuration.enabled)
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
            if self.configuration.today_index and not self._stop.is_set():
                phase = "daily_index"
                _, timings = ensure_daily_index(
                    self.cache, self.transit_database, version, service_date
                )
                daily_ms = timings["total_ms"]
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
                ready=False,
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
            route_count, trip_ids = self.transit_database.active_rail_trip_ids(
                set(services or ())
            )
            before = self.cache.memory_estimate_bytes()
            loader = RequestTripConnectionLoader(
                self.transit_database, shared_cache=self.cache,
                gtfs_version=gtfs_version,
            )
            loader.ensure_loaded(trip_ids)
            self._update(
                skytrain_warmup_complete=True,
                skytrain_warmup_ms=(perf_counter() - started) * 1000,
                skytrain_routes_found=route_count,
                skytrain_active_trips=len(trip_ids),
                skytrain_connections_loaded=loader.connections_loaded,
                skytrain_cache_entries=len(loader.loaded_trip_ids),
                skytrain_cache_memory_bytes=max(
                    0, self.cache.memory_estimate_bytes() - before
                ),
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
            ensure_daily_index(
                self.cache, self.transit_database, state.gtfs_version, tomorrow
            )
            if self.configuration.skytrain and not self._stop.is_set():
                self.warm_skytrain(state.gtfs_version, tomorrow)
        except Exception:
            logger.exception("Background routing cache warm-up failed")
        finally:
            self._update(background_warmup_running=False)
