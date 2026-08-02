"""Exact A* queue ordering for the time-dependent Pareto transit search."""

from __future__ import annotations

import math
import os
from datetime import timedelta
from typing import Any

from .models import Stop
from .reliable import ParetoTransitSearch, _Label

EARTH_RADIUS_KM = 6371.0088
DEFAULT_MAX_POSSIBLE_TRANSIT_SPEED_KMH = 120.0
MAX_SPEED_ENVIRONMENT_VARIABLE = "ROUTING_MAX_TRANSIT_SPEED_KMH"


def configured_max_transit_speed_kmh() -> float:
    """Read the optimistic speed setting, safely retaining the default."""
    raw = os.getenv(MAX_SPEED_ENVIRONMENT_VARIABLE)
    if raw is None:
        return DEFAULT_MAX_POSSIBLE_TRANSIT_SPEED_KMH
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_POSSIBLE_TRANSIT_SPEED_KMH
    return speed if math.isfinite(speed) and speed > 0 else DEFAULT_MAX_POSSIBLE_TRANSIT_SPEED_KMH


def haversine_distance_km(
    latitude1: Any,
    longitude1: Any,
    latitude2: Any,
    longitude2: Any,
) -> float | None:
    """Return great-circle distance, or ``None`` for unusable coordinates."""
    try:
        lat1, lon1, lat2, lon2 = map(
            float, (latitude1, longitude1, latitude2, longitude2)
        )
        if not all(math.isfinite(value) for value in (lat1, lon1, lat2, lon2)):
            return None
        if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
            return None
        if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
            return None
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        intermediate = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        )
        intermediate = min(1.0, max(0.0, intermediate))
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(intermediate))
    except (ArithmeticError, TypeError, ValueError):
        return None


def heuristic_seconds(
    current: Stop | None,
    destination: Stop | None,
    *,
    max_speed_kmh: float = DEFAULT_MAX_POSSIBLE_TRANSIT_SPEED_KMH,
) -> float:
    """Return an optimistic geographic travel time, falling back to zero."""
    if current is None or destination is None:
        return 0.0
    if not math.isfinite(max_speed_kmh) or max_speed_kmh <= 0:
        return 0.0
    distance = haversine_distance_km(
        current.stop_lat,
        current.stop_lon,
        destination.stop_lat,
        destination.stop_lon,
    )
    return 0.0 if distance is None else distance / max_speed_kmh * 3600.0


class AStarParetoTransitSearch(ParetoTransitSearch):
    """Pareto search whose only semantic change is its heap priority."""

    def __init__(
        self,
        database: Any,
        calendar: Any,
        resolver: Any,
        *,
        max_speed_kmh: float | None = None,
    ) -> None:
        super().__init__(database, calendar, resolver)
        self.max_speed_kmh = (
            configured_max_transit_speed_kmh()
            if max_speed_kmh is None
            else max_speed_kmh
        )
        self._destination: Stop | None = None
        self._stops: dict[str, Stop] = {}
        self._heuristic_cache: dict[str, float] = {}
        self._heuristic_cache_hits = 0

    @property
    def algorithm_name(self) -> str:
        return "astar"

    def _prepare_queue_ordering(
        self,
        origin: Stop,
        destination: Stop,
        stop_ids: set[str],
    ) -> None:
        self._destination = destination
        self._heuristic_cache = {}
        self._heuristic_cache_hits = 0
        bulk_lookup = getattr(self.database, "find_stops", None)
        self._stops = bulk_lookup(stop_ids) if callable(bulk_lookup) else {}
        # Legacy repositories cannot expose the departure-window index.  They
        # may still provide one bulk coordinate snapshot; never fall back to a
        # per-label lookup.
        coordinate_snapshot = getattr(self.database, "all_stop_coordinates", None)
        if len(stop_ids) <= 2 and callable(coordinate_snapshot):
            self._stops.update(coordinate_snapshot())
        self._stops[origin.stop_id] = origin
        self._stops[destination.stop_id] = destination

    def _heuristic_seconds(self, stop_id: str) -> float:
        if stop_id in self._heuristic_cache:
            self._heuristic_cache_hits += 1
            return self._heuristic_cache[stop_id]
        value = heuristic_seconds(
            self._stops.get(stop_id),
            self._destination,
            max_speed_kmh=self.max_speed_kmh,
        )
        self._heuristic_cache[stop_id] = value
        return value

    def _queue_priority(self, label: _Label) -> timedelta:
        return label.arrival + timedelta(seconds=self._heuristic_seconds(label.stop_id))

    def _queue_ordering_statistics(self) -> tuple[int, int]:
        return len(self._heuristic_cache), self._heuristic_cache_hits
