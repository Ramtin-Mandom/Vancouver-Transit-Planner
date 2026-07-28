from datetime import date, timedelta

import pytest

from src.routing.models import Itinerary, ReliableAlternative, RouteLeg, Stop
from src.routing.route_results import (
    RoutingPreferences,
    rank_alternatives,
    score_alternative,
)


def alternative(trip_id, minutes, reliability):
    origin = Stop("A", "A")
    destination = Stop("D", "D")
    departure = timedelta(hours=8)
    arrival = departure + timedelta(minutes=minutes)
    itinerary = Itinerary(
        origin,
        destination,
        date(2026, 7, 27),
        departure,
        arrival,
        (
            RouteLeg(
                trip_id,
                trip_id,
                trip_id,
                origin,
                destination,
                departure,
                arrival,
            ),
        ),
    )
    return ReliableAlternative(
        itinerary,
        reliability,
        0.0,
        (),
    )


def test_default_score_is_half_reliability_and_half_speed():
    item = alternative("SLOW", 20, 0.8)
    scored = score_alternative(
        item, timedelta(minutes=10), RoutingPreferences()
    )
    assert scored.speed_component == pytest.approx(0.5)
    assert scored.combined_score == pytest.approx(65.0)


def test_weights_are_normalized_and_validated():
    assert RoutingPreferences(2, 2).normalized_weights == (0.5, 0.5, 0.0)
    with pytest.raises(ValueError):
        RoutingPreferences(-1, 1)
    with pytest.raises(ValueError):
        RoutingPreferences(0, 0, 0)


def test_preferences_change_the_best_route():
    fast = alternative("FAST", 10, 0.4)
    slow = alternative("SLOW", 12, 0.9)
    reliability_first = rank_alternatives(
        (fast, slow),
        preferences=RoutingPreferences(0.9, 0.1),
    )
    speed_first = rank_alternatives(
        (fast, slow),
        preferences=RoutingPreferences(0.1, 0.9),
    )
    assert reliability_first[0].itinerary.legs[0].trip_id == "SLOW"
    assert speed_first[0].itinerary.legs[0].trip_id == "FAST"


@pytest.mark.parametrize(("route_number", "expected"), [(1, 1), (3, 3), (8, 4)])
def test_route_number_limits_and_returns_all_available(route_number, expected):
    items = tuple(
        alternative(f"T{number}", 10 + number, 0.5 + number * 0.1)
        for number in range(4)
    )
    assert len(rank_alternatives(items, route_number=route_number)) == expected


@pytest.mark.parametrize("route_number", [0, -1])
def test_invalid_route_number_is_rejected(route_number):
    with pytest.raises(ValueError, match="route_number"):
        rank_alternatives(
            (alternative("T", 10, 0.9),),
            route_number=route_number,
        )


def test_ties_are_deterministic_by_structural_identity():
    first = alternative("A", 10, 0.8)
    second = alternative("B", 10, 0.8)
    expected = ["A", "B"]
    for items in ((second, first), (first, second)):
        ranked = rank_alternatives(items)
        assert [item.itinerary.legs[0].trip_id for item in ranked] == expected
