from contextlib import contextmanager
from decimal import Decimal

from src.routing.database import TransitDatabase


def test_stop_search_only_returns_stops_with_scheduled_service():
    class Result:
        def fetchall(self):
            return [
                {
                    "stop_id": "12236",
                    "stop_name": "Lincoln Station @ Platform 1",
                    "stop_code": "60093",
                    "stop_lat": Decimal("49.280417"),
                    "stop_lon": Decimal("-122.794097"),
                }
            ]

    class Connection:
        def execute(self, query, parameters):
            assert "EXISTS" in query
            assert "FROM transit.stop_times AS stop_time" in query
            assert "stop_time.stop_id = stop.stop_id" in query
            assert parameters == ("%Lincoln%", 10)
            return Result()

    database = object.__new__(TransitDatabase)

    @contextmanager
    def connection():
        yield Connection()

    database._connection = connection
    matches = database.search_stops("Lincoln", limit=10)

    assert [stop.stop_id for stop in matches] == ["12236"]
