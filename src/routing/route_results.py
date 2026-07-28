"""Scoring, ordering, and result limiting for reliable transit routes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from time import perf_counter
from typing import Any

from .models import (
    Itinerary,
    ReliableAlternative,
    ReliableSearchResult,
)


@dataclass(frozen=True)
class RoutingPreferences:
    """Relative effects used to rank bounded Pareto alternatives."""

    reliability_effect: float = 0.50
    travel_time_effect: float = 0.50
    transfer_effect: float = 0.0

    def __post_init__(self) -> None:
        weights = (
            self.reliability_effect,
            self.travel_time_effect,
            self.transfer_effect,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("routing preference weights cannot be negative")
        if sum(weights) == 0:
            raise ValueError("at least one routing preference weight must be positive")

    @property
    def normalized_weights(self) -> tuple[float, float, float]:
        total = (
            self.reliability_effect
            + self.travel_time_effect
            + self.transfer_effect
        )
        return (
            self.reliability_effect / total,
            self.travel_time_effect / total,
            self.transfer_effect / total,
        )


def itinerary_identity(
    itinerary: Itinerary,
) -> tuple[tuple[str, str, str], ...]:
    """Stable structural identity used as the final ordering tie-breaker."""
    return tuple(
        (leg.trip_id, leg.origin.stop_id, leg.destination.stop_id)
        for leg in itinerary.legs
    )


def score_alternative(
    alternative: ReliableAlternative,
    fastest_duration: timedelta,
    preferences: RoutingPreferences,
) -> ReliableAlternative:
    """Return an immutable alternative scored with normalized preferences."""
    duration_seconds = max(
        1.0, alternative.itinerary.total_scheduled_travel_time.total_seconds()
    )
    fastest_seconds = max(0.0, fastest_duration.total_seconds())
    speed = min(1.0, fastest_seconds / duration_seconds)
    transfer_component = 1.0 / (1.0 + alternative.itinerary.transfer_count)
    reliability_weight, time_weight, transfer_weight = (
        preferences.normalized_weights
    )
    score = 100.0 * (
        reliability_weight * min(1.0, max(0.0, alternative.route_reliability))
        + time_weight * speed
        + transfer_weight * transfer_component
    )
    return replace(
        alternative,
        speed_component=speed,
        combined_score=min(100.0, max(0.0, score)),
    )


def rank_alternatives(
    alternatives: tuple[ReliableAlternative, ...],
    *,
    route_number: int = 5,
    preferences: RoutingPreferences | None = None,
) -> list[ReliableAlternative]:
    """Score, deterministically order, and limit existing alternatives."""
    if route_number < 1:
        raise ValueError("route_number must be at least 1")
    if not alternatives:
        return []
    selected_preferences = preferences or RoutingPreferences()
    fastest = min(
        item.itinerary.total_scheduled_travel_time for item in alternatives
    )
    scored = [
        score_alternative(item, fastest, selected_preferences)
        for item in alternatives
    ]
    scored.sort(
        key=lambda item: (
            -item.combined_score,
            -item.route_reliability,
            item.itinerary.arrival_time,
            item.itinerary.transfer_count,
            itinerary_identity(item.itinerary),
        )
    )
    return scored[:route_number]


def ranked_search_result(
    result: ReliableSearchResult,
    *,
    route_number: int = 5,
    preferences: RoutingPreferences | None = None,
) -> ReliableSearchResult:
    """Add ranking diagnostics while retaining the complete result shape."""
    started = perf_counter()
    alternatives = rank_alternatives(
        result.alternatives,
        route_number=route_number,
        preferences=preferences,
    )
    ranking_ms = (perf_counter() - started) * 1000
    return replace(
        result,
        alternatives=tuple(alternatives),
        timing=replace(
            result.timing,
            ranking_ms=result.timing.ranking_ms + ranking_ms,
            total_ms=result.timing.total_ms + ranking_ms,
        ),
    )


def get_ranked_route_result(
    search: Any,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    departure_time: timedelta,
    *,
    route_number: int = 5,
    preferences: RoutingPreferences | None = None,
    **bounds: Any,
) -> ReliableSearchResult:
    """Run the existing Pareto search, then rank and limit its candidates."""
    if route_number < 1:
        raise ValueError("route_number must be at least 1")
    candidates = search.search(
        origin_stop_id,
        destination_stop_id,
        service_date,
        departure_time,
        limit=None,
        **bounds,
    )
    return ranked_search_result(
        candidates,
        route_number=route_number,
        preferences=preferences,
    )


def get_ranked_routes(
    search: Any,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    departure_time: timedelta,
    *,
    route_number: int = 5,
    preferences: RoutingPreferences | None = None,
    **bounds: Any,
) -> list[ReliableAlternative]:
    """Return the ordered alternatives without search diagnostics."""
    return list(
        get_ranked_route_result(
            search,
            origin_stop_id,
            destination_stop_id,
            service_date,
            departure_time,
            route_number=route_number,
            preferences=preferences,
            **bounds,
        ).alternatives
    )
