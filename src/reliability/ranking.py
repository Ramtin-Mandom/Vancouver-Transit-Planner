"""Reliability-aware itinerary scoring and stable ranking."""

from __future__ import annotations

from .models import RankedItinerary

COMPLETION_WEIGHT = 0.55
SCHEDULE_ADHERENCE_WEIGHT = 0.25
SPEED_WEIGHT = 0.20


def _clamp_probability(value: float) -> float:
    return min(1.0, max(0.0, value))


def itinerary_identifier(itinerary) -> str:
    return "|".join(
        f"{leg.trip_id}:{leg.origin.stop_id}>{leg.destination.stop_id}"
        for leg in itinerary.legs
    )


def reliability_score(itinerary, simulation, fastest_duration) -> float:
    duration = itinerary.total_scheduled_travel_time.total_seconds()
    fastest = fastest_duration.total_seconds()
    ratio = 1.0 if duration <= 0 else _clamp_probability(fastest / duration)
    completion = _clamp_probability(simulation.completion_probability)
    adherence = _clamp_probability(simulation.schedule_adherence)
    # Completion captures transfer success, adherence prevents a direct but
    # chronically early/late route from receiving a perfect score, and speed
    # retains a bounded preference for shorter scheduled journeys.
    score = 100 * (
        COMPLETION_WEIGHT * completion
        + SCHEDULE_ADHERENCE_WEIGHT * adherence
        + SPEED_WEIGHT * ratio
    )
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
            schedule_adherence=_clamp_probability(
                simulation.schedule_adherence
            ),
            speed_ratio=(
                1.0
                if item.total_scheduled_travel_time.total_seconds() <= 0
                else _clamp_probability(
                    fastest.total_seconds()
                    / item.total_scheduled_travel_time.total_seconds()
                )
            ),
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
