import pytest

from src.reliability.models import ReliabilityProfile
from src.reliability.profiles import FALLBACKS, ProfileResolver


def profile(samples=20):
    return ReliabilityProfile("R", "S", 0, 8, samples, 60, 10, 60, 90, 1.0)


@pytest.mark.parametrize("selected_index", range(len(FALLBACKS)))
def test_every_profile_fallback_level(selected_index):
    class Database:
        def __init__(self):
            self.calls = 0

        def profile(self, *args):
            current = self.calls
            self.calls += 1
            return profile() if current == selected_index else None

    selection = ProfileResolver(Database()).resolve("R", "S", 0, 8)
    assert selection.fallback_level == FALLBACKS[selected_index][0]


def test_minimum_samples_and_insufficient_data():
    class Database:
        def profile(self, *args):
            return profile(samples=19)

    selection = ProfileResolver(Database(), minimum_samples=20).resolve(
        "R", "S", 0, 8
    )
    assert selection.insufficient_data
    assert selection.profile is None
