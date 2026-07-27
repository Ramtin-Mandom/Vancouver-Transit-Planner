from datetime import date, timedelta

from src.reliability.models import ProfileSelection, ReliabilityProfile
from src.reliability.simulation import simulate_itinerary
from src.routing.models import Itinerary, RouteLeg, Stop


def itinerary(transfer_minutes=5):
    a, b, c = Stop("A", "A"), Stop("B", "B"), Stop("C", "C")
    legs = (
        RouteLeg("T1", "R1", "1", a, b, timedelta(hours=8), timedelta(hours=8, minutes=10)),
        RouteLeg(
            "T2", "R2", "2", b, c,
            timedelta(hours=8, minutes=10 + transfer_minutes),
            timedelta(hours=8, minutes=30),
        ),
    )
    return Itinerary(a, c, date(2026, 7, 27), timedelta(hours=8), timedelta(hours=8, minutes=30), legs)


class Resolver:
    def __init__(self, delay, insufficient=False):
        self.delay = delay
        self.insufficient = insufficient

    def resolve(self, *args):
        if self.insufficient:
            return ProfileSelection(None, "insufficient-data", True)
        value = ReliabilityProfile("R", "S", 0, 8, 30, self.delay, 0, self.delay, self.delay, 1)
        return ProfileSelection(value, "exact", False)


def test_successful_and_missed_transfer():
    assert simulate_itinerary(itinerary(), Resolver(60), simulations=10).completion_probability == 1
    assert simulate_itinerary(itinerary(), Resolver(600), simulations=10).completion_probability == 0


def test_fixed_seed_is_deterministic_and_insufficient_is_flagged():
    first = simulate_itinerary(itinerary(), Resolver(0, True), simulations=50, seed=7)
    second = simulate_itinerary(itinerary(), Resolver(0, True), simulations=50, seed=7)
    assert first == second
    assert first.insufficient_data
