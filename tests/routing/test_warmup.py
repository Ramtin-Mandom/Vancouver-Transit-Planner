from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date

import pytest

from src.routing.cache import CacheConfiguration, RoutingCacheManager
from src.routing.warmup import (
    RoutingWarmupCoordinator,
    WarmupConfiguration,
    ensure_stop_departures,
)
from src.routing.database import TransitDatabase
from tests.routing.test_planner import at, connection


TODAY = date(2026, 7, 27)


class WarmupRepository:
    def __init__(self):
        self.daily_queries = 0
        self.rail_queries = 0
        self.trip_queries = 0
        self.daily_row = connection(
            "RAIL", "weekday", "SKY", "A", "B", at(25, 10), at(25, 20), 1
        )

    def gtfs_version(self):
        return "feed-1"

    def all_stop_coordinates(self):
        return {}

    def bulk_transfers(self):
        return []

    def active_service_ids(self, service_date):
        return {"weekday"}

    def bulk_daily_departures(self, *, service_ids):
        self.daily_queries += 1
        assert service_ids == {"weekday"}
        return [self.daily_row]

    def daily_departures_from_stop(self, stop_id, *, service_ids):
        self.daily_queries += 1
        assert service_ids == {"weekday"}
        return [self.daily_row] if stop_id == "A" else []

    def active_rail_trip_ids(self, service_ids):
        self.rail_queries += 1
        assert service_ids == {"weekday"}
        return 1, {"RAIL"}

    def bulk_trip_connections(self, trip_ids, *, batch_size):
        self.trip_queries += 1
        return [self.daily_row] if "RAIL" in trip_ids else []


def cache():
    return RoutingCacheManager(CacheConfiguration(response_enabled=False))


def test_default_warmup_avoids_daily_index_and_skytrain_preload():
    repository = WarmupRepository()
    shared = cache()
    coordinator = RoutingWarmupCoordinator(
        shared, repository, object(), WarmupConfiguration()
    )
    first = coordinator.warm_essential(TODAY)
    second = coordinator.warm_essential(TODAY)
    assert first.ready and second.ready
    assert repository.daily_queries == 0
    assert repository.rail_queries == 0
    assert repository.trip_queries == 0
    assert not shared.trips.get(("feed-1", "RAIL"))[0]


def test_concurrent_stop_departure_load_is_single_flight():
    repository = WarmupRepository()
    shared = cache()
    coordinator = RoutingWarmupCoordinator(
        shared, repository, object(), WarmupConfiguration(today_index=False)
    )
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(
            lambda _: ensure_stop_departures(
                shared, repository, "feed-1", TODAY, "A", {"weekday"}
            ),
            range(12),
        ))
    assert repository.daily_queries == 1
    found, cached = shared.departures.get(("feed-1", TODAY, "A"))
    assert found and cached.window(at(25), at(26)) == (repository.daily_row,)


def test_optional_skytrain_warmup_respects_trip_limit():
    repository = WarmupRepository()
    repository.active_rail_trip_ids = lambda services: (1, {"A", "B", "C"})
    loaded = []

    def load(trip_ids, *, batch_size):
        loaded.extend(trip_ids)
        return []

    repository.bulk_trip_connections = load
    coordinator = RoutingWarmupCoordinator(
        cache(), repository, object(),
        WarmupConfiguration(skytrain=True, skytrain_max_trips=2),
    )
    coordinator.warm_essential(TODAY)
    assert set(loaded) == {"A", "B"}
    assert len(loaded) == 2


def test_failed_warmup_does_not_publish_partial_static_snapshot():
    class Broken(WarmupRepository):
        def bulk_transfers(self):
            raise RuntimeError("broken transfers")

    repository = Broken()
    shared = cache()
    coordinator = RoutingWarmupCoordinator(shared, repository, object())
    with pytest.raises(RuntimeError):
        coordinator.warm_essential(TODAY)
    assert not shared.static_snapshots.get("feed-1")[0]
    assert coordinator.state().warmup_failed


def test_version_change_builds_distinct_stop_entries():
    repository = WarmupRepository()
    shared = cache()
    ensure_stop_departures(shared, repository, "feed-1", TODAY, "A", {"weekday"})
    ensure_stop_departures(shared, repository, "feed-2", TODAY, "A", {"weekday"})
    assert repository.daily_queries == 2


def test_background_warmup_stops_cleanly_before_starting_work():
    repository = WarmupRepository()
    shared = cache()
    coordinator = RoutingWarmupCoordinator(
        shared, repository, object(),
        WarmupConfiguration(today_index=False, tomorrow_index=True),
    )
    coordinator.warm_essential(TODAY)
    queries_before_stop = repository.daily_queries
    coordinator.request_stop()
    coordinator.warm_tomorrow()
    assert repository.daily_queries == queries_before_stop
    assert not coordinator.state().background_warmup_running


def test_skytrain_repository_selection_uses_gtfs_route_type_one():
    class Result:
        def fetchall(self):
            return [
                {"trip_id": "T1", "route_id": "R1"},
                {"trip_id": "T2", "route_id": "R1"},
            ]

    class Connection:
        query = ""
        params = None

        def execute(self, query, params):
            self.query = query
            self.params = params
            return Result()

    connection = Connection()

    class Repository(TransitDatabase):
        @contextmanager
        def _connection(self):
            yield connection

    repository = Repository(config=object())
    route_count, trips = repository.active_rail_trip_ids({"weekday"})
    assert "route.route_type = 1" in connection.query
    assert connection.params == (["weekday"],)
    assert route_count == 1
    assert trips == {"T1", "T2"}
