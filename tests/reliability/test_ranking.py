from datetime import date, timedelta

from src.reliability.models import SimulationResult
from src.reliability.ranking import rank_itineraries
from src.routing.models import Itinerary, RouteLeg, Stop


def journey(trip, minutes):
    a, b = Stop("A", "A"), Stop("B", "B")
    leg = RouteLeg(trip, "R", "R", a, b, timedelta(hours=8), timedelta(hours=8, minutes=minutes))
    return Itinerary(a, b, date(2026, 7, 27), timedelta(hours=8), leg.arrival_time, (leg,))


def simulation(completion):
    return SimulationResult(completion, completion, 0, 0, False, ("exact",))


def test_slower_reliable_route_can_rank_above_fast_unreliable_route():
    fast, slow = journey("FAST", 10), journey("SLOW", 12)
    ranked = rank_itineraries([fast, slow], [simulation(0.2), simulation(1.0)])
    assert ranked[0].itinerary.legs[0].trip_id == "SLOW"


def test_stable_tie_breaking_uses_identifier():
    a, b = journey("A", 10), journey("B", 10)
    ranked = rank_itineraries([b, a], [simulation(1), simulation(1)])
    assert [item.itinerary_id for item in ranked] == sorted(
        item.itinerary_id for item in ranked
    )
