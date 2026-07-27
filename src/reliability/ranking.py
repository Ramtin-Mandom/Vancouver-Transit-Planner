"""Reliability-aware itinerary scoring and stable ranking."""

from __future__ import annotations

from .models import RankedItinerary


def itinerary_identifier(itinerary) -> str:
    return "|".join(
        f"{leg.trip_id}:{leg.origin.stop_id}>{leg.destination.stop_id}"
        for leg in itinerary.legs
    )


def reliability_score(itinerary, simulation, fastest_duration) -> float:
    duration = itinerary.total_scheduled_travel_time.total_seconds()
    fastest = fastest_duration.total_seconds()
    ratio = 1.0 if duration <= 0 else min(1.0, max(0.0, fastest / duration))
    score = 100 * (0.70 * simulation.completion_probability + 0.30 * ratio)
    return min(100.0, max(0.0, score))


def rank_itineraries(itineraries, simulations) -> list[RankedItinerary]:
    if len(itineraries) != len(simulations):
        raise ValueError("each itinerary must have one simulation result")
    if not itineraries:
        return []
    fastest = min(item.total_scheduled_travel_time for item in itineraries)
    ranked = [
        RankedItinerary(
            itinerary=item,
            simulation=simulation,
            reliability_score=reliability_score(item, simulation, fastest),
            itinerary_id=itinerary_identifier(item),
        )
        for item, simulation in zip(itineraries, simulations)
    ]
    return sorted(
        ranked,
        key=lambda item: (
            -item.reliability_score,
            -item.simulation.completion_probability,
            item.itinerary.arrival_time,
            item.itinerary.transfer_count,
            item.itinerary_id,
        ),
    )
