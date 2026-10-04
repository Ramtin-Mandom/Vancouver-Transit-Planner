import zipfile
from datetime import timedelta

import pytest

from scripts.restore_routing_snapshot import DEFAULT_ARCHIVE, restore
from src.routing.snapshot import RoutingSnapshot, SnapshotPlanner


def test_repository_archive_restores_without_database(tmp_path, monkeypatch):
    def no_database(*args, **kwargs):
        raise AssertionError("Offline deployment must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_database)
    output = tmp_path / "snapshot"
    restore(DEFAULT_ARCHIVE, output)
    snapshot = RoutingSnapshot(output)
    try:
        assert snapshot.manifest["counts"]["stops"] == 8717
        assert len(snapshot.arrays["profile_probability"]) == 1939
        arrays = snapshot.arrays
        index = 0
        origin = snapshot.stop(int(arrays["from_stop"][index])).stop_id
        destination = snapshot.stop(int(arrays["to_stop"][index])).stop_id
        result = SnapshotPlanner(snapshot).get_ranked_route_result(
            origin,
            destination,
            None,
            timedelta(seconds=int(arrays["departure_seconds"][index])),
        )
        assert result.alternatives
    finally:
        snapshot.close()


def test_invalid_archive_does_not_publish_snapshot(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("../escape.npy", "invalid")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="flat snapshot"):
        restore(archive, output)
    assert not output.exists()
    assert not (tmp_path / "escape.npy").exists()
