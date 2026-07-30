"""Constant-cost hierarchical lookup of precomputed reliability profiles."""

from __future__ import annotations

from datetime import timedelta

from .classification import time_window
from .models import ProfileSelection, ReliabilityProfile
from .policy import DEFAULT_MINIMUM_SAMPLES

FALLBACKS = ("route_direction_window", "route_direction", "route", "network")


class ProfileResolver:
    def __init__(
        self,
        database,
        minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
    ) -> None:
        self.database = database
        self.minimum_samples = minimum_samples
        self._cache = {}

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

        profile = self.database.profile(route_id, direction_id, window)
        if profile is not None:
            selection = ProfileSelection(
                profile, "route_direction_window",
                profile.sample_count < self.minimum_samples,
            )
        else:
            # Exact cells can be absent. Materialize a lightweight profile from
            # the first precomputed parent so routing still uses the hierarchy.
            level = "route_direction"
            parent = self.database.fallback_profile(level, route_id, direction_id)
            if parent is None:
                level = "route"
                parent = self.database.fallback_profile(level, route_id, None)
            if parent is None:
                level = "network"
                parent = self.database.fallback_profile(level, None, None)
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
        self._cache[key] = selection
        return selection
