from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import gc
import weakref

from src.routing.cache import BoundedTTLCache, CacheConfiguration, RoutingCacheManager
from src.routing.search_data import RequestTripConnectionLoader, SearchDataIndex
from tests.routing.test_planner import at, connection


def manager(**overrides):
    values = vars(CacheConfiguration()) | overrides
    return RoutingCacheManager(CacheConfiguration(**values))


def test_lru_capacity_and_eviction_are_measurable():
    cache = BoundedTTLCache[str, int](2, 60)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == (True, 1)
    cache.put("c", 3)
    assert cache.get("b") == (False, None)
    assert cache.statistics().evictions == 1


def test_eviction_releases_the_cached_value_reference():
    class Value:
        pass

    cache = BoundedTTLCache[str, Value](1, 60)
    value = Value()
    reference = weakref.ref(value)
    cache.put("old", value)
    del value
    cache.put("new", Value())
    gc.collect()
    assert reference() is None


def test_trip_cache_reuses_rows_and_versions_make_old_entries_unreachable():
    row = connection("T", "weekday", "R", "A", "B", at(25, 10), at(25, 20), 1)

    class Repository:
        calls = 0

        def bulk_trip_connections(self, trip_ids, *, batch_size):
            self.calls += 1
            return [row] if "T" in trip_ids else []

    repository = Repository()
    shared = manager()
    RequestTripConnectionLoader(repository, shared_cache=shared, gtfs_version="v1").ensure_loaded({"T"})
    warm = RequestTripConnectionLoader(repository, shared_cache=shared, gtfs_version="v1")
    warm.ensure_loaded({"T"})
    assert repository.calls == 1
    assert warm.shared_cache_hits == 1
    RequestTripConnectionLoader(repository, shared_cache=shared, gtfs_version="v2").ensure_loaded({"T"})
    assert repository.calls == 2


def test_missing_trip_uses_short_lived_negative_cache():
    class Repository:
        calls = 0

        def bulk_trip_connections(self, trip_ids, *, batch_size):
            self.calls += 1
            return []

    repository = Repository()
    shared = manager()
    RequestTripConnectionLoader(repository, shared_cache=shared, gtfs_version="v1").ensure_loaded({"missing"})
    warm = RequestTripConnectionLoader(repository, shared_cache=shared, gtfs_version="v1")
    warm.ensure_loaded({"missing"})
    assert repository.calls == 1
    assert warm.negative_cache_hits == 1


def test_daily_index_keeps_gtfs_times_beyond_24_hours():
    item = connection("T", "weekday", "R", "A", "B", at(25, 10), at(25, 20), 1)
    index = SearchDataIndex.build([item], [], [])
    assert index.departures("A", timedelta(hours=25), timedelta(hours=26)) == (item,)


def test_single_flight_never_publishes_partial_values():
    shared = manager()
    builds = 0

    def get_value():
        nonlocal builds

        def load():
            nonlocal builds
            found, value = shared.departures.get(("v1", "date", "A"))
            if found:
                return value
            builds += 1
            value = tuple(range(100))
            shared.departures.put(("v1", "date", "A"), value)
            return value

        return shared.single_flight("daily", ("v1", "date"), load)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: get_value(), range(16)))
    assert builds == 1
    assert all(value == tuple(range(100)) for value in values)
