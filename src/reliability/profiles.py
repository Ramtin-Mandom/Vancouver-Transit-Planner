"""Constant-cost hierarchical lookup of precomputed reliability profiles."""

from __future__ import annotations

from datetime import timedelta

from .classification import time_window
from .models import ProfileSelection, ReliabilityProfile
from .policy import DEFAULT_MINIMUM_SAMPLES
from src.routing.cache import RoutingCacheManager

FALLBACKS = ("route_direction_window", "route_direction", "route", "network")


class ProfileResolver:
    def __init__(
        self,
        database,
        minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
        *, shared_cache: RoutingCacheManager | None = None,
        profile_version: str = "unknown",
    ) -> None:
        self.database = database
        self.minimum_samples = minimum_samples
        self._cache = {}
        self.shared_cache = shared_cache
        self.profile_version = profile_version
        self.shared_cache_hits = 0
        self.shared_cache_misses = 0

    def preload(self, keys: set[tuple[str, int | None, str]]) -> int:
        """Populate the request-local resolver cache with two bulk queries."""
        loader = getattr(self.database, "bulk_profile_data", None)
        if not callable(loader) or not keys:
            return 0
        missing = set()
        raw_by_key = {}
        for key in keys:
            cache_key = (self.profile_version, *key)
            found, raw = (
                self.shared_cache.profiles.get(cache_key)
                if self.shared_cache is not None else (False, None)
            )
            if found:
                self.shared_cache_hits += 1
                raw_by_key[key] = raw
            else:
                if self.shared_cache is not None:
                    self.shared_cache_misses += 1
                missing.add(key)
        query_count = 0
        if missing:
            def load_missing():
                loaded = {}
                remaining = set(missing)
                if self.shared_cache is not None:
                    for missing_key in tuple(remaining):
                        found, cached = self.shared_cache.profiles.get(
                            (self.profile_version, *missing_key)
                        )
                        if found:
                            loaded[missing_key] = cached
                            remaining.remove(missing_key)
                exact, parents = loader(remaining) if remaining else ({}, {})
                for missing_key in remaining:
                    route_id, direction_id, window = missing_key
                    raw = (
                        exact.get(missing_key),
                        parents.get(("route_direction", route_id, -1 if direction_id is None else direction_id)),
                        parents.get(("route", route_id, -1)),
                        parents.get(("network", "*", -1)),
                    )
                    loaded[missing_key] = raw
                    if self.shared_cache is not None:
                        self.shared_cache.profiles.put(
                            (self.profile_version, *missing_key), raw
                        )
                return loaded

            if self.shared_cache is not None:
                loaded = self.shared_cache.single_flight(
                    "profiles",
                    (self.profile_version, tuple(sorted(missing, key=repr))),
                    load_missing,
                )
            else:
                loaded = load_missing()
            raw_by_key.update(loaded)
            query_count = 2
        for key in keys:
            route_id, direction_id, window = key
            profile, direction_parent, route_parent, network_parent = raw_by_key[key]
            parent_values = {
                ("route_direction", route_id, -1 if direction_id is None else direction_id): direction_parent,
                ("route", route_id, -1): route_parent,
                ("network", "*", -1): network_parent,
            }
            self._cache[key] = self._select(
                route_id,
                direction_id,
                profile,
                lambda level, route, direction: parent_values.get((
                    level, route or "*", -1 if direction is None else direction
                )),
            )
        return query_count

    def set_statement_timeout(self, milliseconds: int) -> None:
        configure = getattr(self.database, "set_statement_timeout", None)
        if callable(configure):
            configure(milliseconds)

    def resolve(
        self, route_id: str, direction_id: int | None, scheduled_time
    ) -> ProfileSelection:
        window = (
            time_window(scheduled_time)
            if isinstance(scheduled_time, timedelta)
            else str(scheduled_time)
        )
        key = (route_id, direction_id, window)
        if key in self._cache:
            return self._cache[key]

        cache_key = (self.profile_version, *key)
        if self.shared_cache is not None:
            found, raw = self.shared_cache.profiles.get(cache_key)
            if found and raw is not None:
                self.shared_cache_hits += 1
                profile, direction_parent, route_parent, network_parent = raw
                parent_values = {
                    ("route_direction", route_id, -1 if direction_id is None else direction_id): direction_parent,
                    ("route", route_id, -1): route_parent,
                    ("network", "*", -1): network_parent,
                }
                selection = self._select(
                    route_id, direction_id, profile,
                    lambda level, route, direction: parent_values.get((
                        level, route or "*", -1 if direction is None else direction
                    )),
                )
                self._cache[key] = selection
                return selection
            self.shared_cache_misses += 1

        profile = self.database.profile(route_id, direction_id, window)
        fallback_lookup = getattr(self.database, "fallback_profile", lambda *args: None)
        direction_parent = fallback_lookup("route_direction", route_id, direction_id)
        route_parent = fallback_lookup("route", route_id, None)
        network_parent = fallback_lookup("network", None, None)
        if self.shared_cache is not None:
            self.shared_cache.profiles.put(
                cache_key, (profile, direction_parent, route_parent, network_parent)
            )
        parent_values = {
            ("route_direction", route_id, -1 if direction_id is None else direction_id): direction_parent,
            ("route", route_id, -1): route_parent,
            ("network", "*", -1): network_parent,
        }
        selection = self._select(
            route_id,
            direction_id,
            profile,
            lambda level, route, direction: parent_values.get((
                level, route or "*", -1 if direction is None else direction
            )),
        )
        self._cache[key] = selection
        return selection

    def _select(self, route_id, direction_id, profile, fallback_lookup):
        if profile is not None:
            selection = ProfileSelection(
                profile, "route_direction_window",
                profile.sample_count < self.minimum_samples,
            )
        else:
            # Exact cells can be absent. Materialize a lightweight profile from
            # the first precomputed parent so routing still uses the hierarchy.
            level = "route_direction"
            parent = fallback_lookup(level, route_id, direction_id)
            if parent is None:
                level = "route"
                parent = fallback_lookup(level, route_id, None)
            if parent is None:
                level = "network"
                parent = fallback_lookup(level, None, None)
            fallback = None
            if parent:
                probability = float(parent["reliability_probability"])
                fallback = ReliabilityProfile(
                    route_id=route_id if level != "network" else None,
                    stop_id=None,
                    weekday=None,
                    hour_of_day=None,
                    sample_count=int(parent["sample_count"]),
                    mean_delay_seconds=0.0,
                    mean_absolute_delay_seconds=0.0,
                    delay_stddev_seconds=None,
                    p50_delay_seconds=0.0,
                    p90_delay_seconds=0.0,
                    early_probability=0.0,
                    on_time_probability=float(parent["on_time_probability"]),
                    late_probability=(
                        1.0 - float(parent["on_time_probability"])
                    ),
                    direction_id=direction_id if level == "route_direction" else None,
                    time_window=None,
                    reliability_probability=probability,
                    distinct_service_dates=int(parent["distinct_service_dates"]),
                )
            selection = ProfileSelection(
                fallback, level if parent else "insufficient-data", True
            )
        return selection
