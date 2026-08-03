from datetime import timedelta

import pytest

from src.reliability.models import ReliabilityProfile
from src.reliability.profiles import FALLBACKS, ProfileResolver
from src.routing.cache import RoutingCacheManager


def profile(samples=20):
    return ReliabilityProfile(
        route_id="R",
        stop_id=None,
        weekday=None,
        hour_of_day=None,
        sample_count=samples,
        mean_delay_seconds=60,
        mean_absolute_delay_seconds=90,
        delay_stddev_seconds=10,
        p50_delay_seconds=60,
        p90_delay_seconds=90,
        early_probability=0.1,
        on_time_probability=0.8,
        late_probability=0.1,
        direction_id=0,
        time_window="morning_peak",
        reliability_probability=0.8,
        distinct_service_dates=10,
    )


def fallback_row():
    return {
        "sample_count": 20,
        "distinct_service_dates": 10,
        "on_time_probability": 0.8,
        "reliability_probability": 0.8,
    }


@pytest.mark.parametrize("selected_level", FALLBACKS)
def test_every_profile_fallback_level(selected_level):
    class Database:
        def profile(self, *args):
            return profile() if selected_level == "route_direction_window" else None

        def fallback_profile(self, level, *args):
            return fallback_row() if level == selected_level else None

    selection = ProfileResolver(Database()).resolve(
        "R", 0, timedelta(hours=8)
    )
    assert selection.fallback_level == selected_level
    assert selection.profile is not None


def test_minimum_samples_and_insufficient_data():
    class Database:
        def profile(self, *args):
            return profile(samples=19)

    selection = ProfileResolver(Database(), minimum_samples=20).resolve(
        "R", 0, timedelta(hours=8)
    )
    assert selection.insufficient_data
    assert selection.profile is not None
    assert selection.profile.sample_count == 19


def test_bulk_preload_preserves_fallback_and_deduplicates_keys():
    class Database:
        calls = 0

        def bulk_profile_data(self, keys):
            self.calls += 1
            return {}, {
                ("route", "R", -1): fallback_row(),
                ("network", "*", -1): fallback_row(),
            }

        def profile(self, *args):
            raise AssertionError("preloaded keys must not query individually")

    database = Database()
    resolver = ProfileResolver(database)
    keys = {("R", 0, "morning_peak"), ("R", 0, "morning_peak")}
    assert resolver.preload(keys) == 2
    first = resolver.resolve("R", 0, timedelta(hours=8))
    second = resolver.resolve("R", 0, timedelta(hours=8))
    assert first is second
    assert first.fallback_level == "route"
    assert database.calls == 1


def test_shared_raw_profiles_do_not_share_minimum_sample_selection():
    class Database:
        calls = 0

        def bulk_profile_data(self, keys):
            self.calls += 1
            key = next(iter(keys))
            return {key: profile(samples=20)}, {}

    database = Database()
    shared = RoutingCacheManager()
    key = {("R", 0, "morning_peak")}
    permissive = ProfileResolver(
        database, minimum_samples=10, shared_cache=shared, profile_version="p1"
    )
    strict = ProfileResolver(
        database, minimum_samples=30, shared_cache=shared, profile_version="p1"
    )
    permissive.preload(key)
    strict.preload(key)
    assert not permissive.resolve("R", 0, timedelta(hours=8)).insufficient_data
    assert strict.resolve("R", 0, timedelta(hours=8)).insufficient_data
    assert database.calls == 1


def test_shared_profile_cache_key_has_no_service_date_or_weekday():
    class Database:
        calls = 0

        def profile(self, *args):
            self.calls += 1
            return profile()

        def fallback_profile(self, *args):
            return None

    database = Database()
    shared = RoutingCacheManager()
    first = ProfileResolver(database, shared_cache=shared, profile_version="p1")
    second = ProfileResolver(database, shared_cache=shared, profile_version="p1")
    first.resolve("R", 0, timedelta(hours=8))
    second.resolve("R", 0, timedelta(hours=8))
    assert database.calls == 1
    assert tuple(shared.profiles._items)[0] == ("p1", "R", 0, "morning_peak")
