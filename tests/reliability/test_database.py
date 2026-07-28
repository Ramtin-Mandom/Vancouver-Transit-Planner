from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from src.reliability.database import ReliabilityDatabase
from src.reliability.models import DelayObservation


def test_schema_uses_full_snapshot_identity_and_idempotent_insert():
    root = Path(__file__).resolve().parents[2]
    schema = (root / "database" / "schema.sql").read_text(encoding="utf-8")
    source = (root / "src" / "reliability" / "database.py").read_text(
        encoding="utf-8"
    )
    assert "trip_id, stop_id, stop_sequence, service_date, observed_at" in schema
    assert "ON CONFLICT" in source
    assert "executemany" in source


def test_aggregation_selects_latest_observation_only():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "reliability" / "database.py").read_text(
        encoding="utf-8"
    )
    assert "DISTINCT ON" in source
    assert "observed_at DESC" in source


def test_profile_lookup_reads_materialized_profiles():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "reliability" / "database.py").read_text(
        encoding="utf-8"
    )
    profile_source = source[source.index("    def profile("):]
    assert "FROM transit.route_reliability" in profile_source
    assert "FROM latest" not in profile_source.split(
        "    def route_profiles", 1
    )[0]


def test_duplicate_snapshot_count_uses_batch_insert_result():
    class Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def executemany(self, query, rows):
            assert "ON CONFLICT" in query
            assert len(rows) == 2

    class Connection:
        def cursor(self):
            return Cursor()

    database = object.__new__(ReliabilityDatabase)

    @contextmanager
    def connection(readonly=True):
        assert not readonly
        yield Connection()

    database.connection = connection
    observation = DelayObservation(
        "T", "S", 1, date(2026, 1, 1), timedelta(hours=25),
        datetime(2026, 1, 2, tzinfo=timezone.utc), 30,
    )
    inserted, duplicates = database.insert_observations(
        [observation, observation]
    )
    assert (inserted, duplicates) == (1, 1)
