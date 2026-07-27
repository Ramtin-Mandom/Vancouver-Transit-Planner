from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace

from src.reliability.collector import collect_snapshot
from src.reliability.models import ParseSummary


def test_collector_coordinates_batch_insert(monkeypatch):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parsed = ParseSummary(timestamp, (), 2, 3, 1, 1, 1)
    monkeypatch.setattr("src.reliability.collector.decode_feed", lambda data: "feed")
    monkeypatch.setattr(
        "src.reliability.collector.parse_feed", lambda feed, database: parsed
    )

    class Client:
        def download(self):
            return b"protobuf"

    class Database:
        def insert_observations(self, observations):
            return 0, 0

    summary = collect_snapshot(Client(), Database())
    assert summary.trip_updates_processed == 2
    assert summary.malformed == 1


def test_collector_preloads_all_trip_schedules_in_one_lookup_session(monkeypatch):
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parsed = ParseSummary(timestamp, (), 1, 1, 0, 0, 0)

    class Entity(SimpleNamespace):
        def HasField(self, name):
            return name == "trip_update"

    fake_feed = SimpleNamespace(
        entity=[
            Entity(
                trip_update=SimpleNamespace(
                    trip=SimpleNamespace(trip_id="T1")
                )
            ),
            Entity(
                trip_update=SimpleNamespace(
                    trip=SimpleNamespace(trip_id="T2")
                )
            ),
        ]
    )
    monkeypatch.setattr(
        "src.reliability.collector.decode_feed", lambda data: fake_feed
    )
    monkeypatch.setattr(
        "src.reliability.collector.parse_feed", lambda feed, database: parsed
    )

    class Client:
        def download(self):
            return b"protobuf"

    class Database:
        def __init__(self):
            self.sessions = 0
            self.preloaded = None

        @contextmanager
        def lookup_session(self):
            self.sessions += 1
            yield

        def preload_schedules(self, trip_ids):
            self.preloaded = set(trip_ids)

        def insert_observations(self, observations):
            return 0, 0

    database = Database()
    collect_snapshot(Client(), database)
    assert database.sessions == 1
    assert database.preloaded == {"T1", "T2"}
