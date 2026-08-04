import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import ApiServices, create_services
from src.api.main import app
from src.routing.snapshot import SnapshotError, build_snapshot_from_rows
from tests.routing.test_snapshot import CONNECTIONS, STOPS


def _render_environment(tmp_path, fixture_path, wrapper):
    return {
        **os.environ,
        "PYTHON_BIN": str(wrapper),
        "REAL_PYTHON": sys.executable,
        "ROUTING_SNAPSHOT_PATH": str(tmp_path / "built-snapshot"),
        "ROUTING_SNAPSHOT_FIXTURE_PATH": str(fixture_path),
        "DB_HOST": "configured",
        "DB_PORT": "5432",
        "DB_NAME": "configured",
        "DB_USER": "configured",
        "DB_PASSWORD": "secret-not-logged",
    }


@pytest.fixture
def render_shell_fixture(tmp_path):
    if os.name == "nt" or not Path("/bin/bash").exists():
        pytest.skip("Render build workflow test requires a POSIX shell")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"stops": STOPS, "connections": CONNECTIONS}))
    wrapper = tmp_path / "python-wrapper"
    wrapper.write_text(
        """#!/usr/bin/env bash
if [[ "$1 $2" == "-m pip" ]]; then exit 0; fi
if [[ "${FAIL_VALIDATION:-}" == "1" && "$1 $2" == "-m scripts.validate_routing_snapshot" ]]; then exit 42; fi
exec "$REAL_PYTHON" "$@"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return fixture, wrapper


def test_successful_render_build_workflow_with_fixture(tmp_path, render_shell_fixture):
    fixture, wrapper = render_shell_fixture
    environment = _render_environment(tmp_path, fixture, wrapper)
    result = subprocess.run(
        ["/bin/bash", "scripts/render_build.sh"],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "built-snapshot" / "manifest.json").is_file()
    assert "secret-not-logged" not in result.stdout + result.stderr
    assert "completed successfully" in result.stdout


def test_render_build_fails_without_database_configuration(
    tmp_path, render_shell_fixture
):
    fixture, wrapper = render_shell_fixture
    environment = _render_environment(tmp_path, fixture, wrapper)
    environment.pop("DB_PASSWORD")
    result = subprocess.run(
        ["/bin/bash", "scripts/render_build.sh"],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "DB_PASSWORD is not set" in result.stderr
    assert not (tmp_path / "built-snapshot").exists()


def test_render_validation_failure_stops_build(tmp_path, render_shell_fixture):
    fixture, wrapper = render_shell_fixture
    environment = _render_environment(tmp_path, fixture, wrapper)
    environment["FAIL_VALIDATION"] = "1"
    result = subprocess.run(
        ["/bin/bash", "scripts/render_build.sh"],
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 42
    assert "completed successfully" not in result.stdout


def test_blueprint_and_build_script_are_production_safe():
    root = Path(__file__).parents[1]
    blueprint = (root / "render.yaml").read_text(encoding="utf-8")
    build = (root / "scripts" / "render_build.sh").read_text(encoding="utf-8")
    assert blueprint.count("branch: main") == 2
    assert "branch: debug" not in blueprint
    assert "bash scripts/render_build.sh" in blueprint
    assert "--workers 1" in blueprint
    assert "healthCheckPath: /ready" in blueprint
    assert "runtime: static" in blueprint
    assert "VITE_API_BASE_URL" in blueprint
    assert "API_CORS_ORIGINS" in blueprint
    assert "DB_PASSWORD\n        sync: false" in blueprint
    assert "set -euo pipefail" in build
    assert "uvicorn" not in build
    assert "DB_PASSWORD" in build
    assert "${!variable" in build  # checks presence, never echoes the value


def test_snapshot_runtime_uses_no_database_and_is_ready(tmp_path, monkeypatch):
    path = tmp_path / "snapshot"
    build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(path))
    monkeypatch.setenv("ROUTING_SNAPSHOT_REQUIRED", "true")
    monkeypatch.setenv("ROUTING_SNAPSHOT_DEVELOPMENT_FALLBACK", "false")

    def database_forbidden(*args, **kwargs):
        raise AssertionError("runtime PostgreSQL access is forbidden")

    monkeypatch.setattr("src.api.dependencies.TransitDatabase", database_forbidden)
    monkeypatch.setattr("src.api.dependencies.ReliabilityDatabase", database_forbidden)
    services = create_services()
    assert services.warmup is None
    app.state.services = services
    app.state.owns_services = False
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            assert client.get("/health").json() == {"status": "ok"}
            ready = client.get("/ready").json()
            assert ready["snapshot_loaded"] is True
            assert (
                client.get("/stops/search", params={"query": "alp"}).status_code == 200
            )
            response = client.post(
                "/routes/plan",
                json={
                    "origin_stop_id": "A",
                    "destination_stop_id": "C",
                    "departure_time": "08:00:00",
                    "include_alternatives": False,
                    "minimum_samples": 20,
                    "max_extra_minutes": 30,
                    "search_timeout_seconds": 30,
                    "reliability_effect": 0.5,
                    "travel_time_effect": 0.5,
                    "transfer_effect": 0,
                },
            )
            assert response.status_code == 200
    finally:
        app.state.services = None
        services.close()


def test_production_missing_snapshot_never_activates_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(tmp_path / "missing"))
    monkeypatch.setenv("ROUTING_SNAPSHOT_REQUIRED", "true")
    monkeypatch.setenv("ROUTING_SNAPSHOT_DEVELOPMENT_FALLBACK", "false")
    monkeypatch.setattr(
        "src.api.dependencies.TransitDatabase",
        lambda: (_ for _ in ()).throw(AssertionError("legacy fallback activated")),
    )
    with pytest.raises(SnapshotError):
        create_services()


def test_ready_returns_503_when_routing_is_unavailable():
    app.state.services = ApiServices(
        None, None, None, routing_unavailable_reason="routing snapshot missing"
    )
    app.state.owns_services = False
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    app.state.services = None


def test_expired_snapshot_is_unready_and_route_error_is_explicit(tmp_path, monkeypatch):
    from datetime import date

    path = tmp_path / "expired"
    calendars = [
        {
            "service_id": "S",
            "start_date": date(2020, 1, 1),
            "end_date": date(2020, 1, 31),
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": True,
        }
    ]
    build_snapshot_from_rows(
        path, stops=STOPS, connections=CONNECTIONS, calendars=calendars
    )
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(path))
    services = create_services()
    app.state.services = services
    app.state.owns_services = False
    try:
        with TestClient(app) as client:
            readiness = client.get("/ready")
            assert readiness.status_code == 503
            assert readiness.json()["service_range"] == {
                "earliest_date": "2020-01-01",
                "latest_date": "2020-01-31",
            }
            response = client.post(
                "/routes/plan",
                json={
                    "origin_stop_id": "A",
                    "destination_stop_id": "C",
                    "departure_time": "08:00:00",
                    "include_alternatives": False,
                    "minimum_samples": 20,
                    "max_extra_minutes": 30,
                    "search_timeout_seconds": 30,
                    "reliability_effect": 0.5,
                    "travel_time_effect": 0.5,
                    "transfer_effect": 0,
                },
            )
            assert response.status_code == 503
            assert "GTFS feed expired" in response.json()["detail"]
    finally:
        app.state.services = None
        services.close()


def test_render_manifest_contains_build_measurements(tmp_path):
    path = tmp_path / "snapshot"
    report = build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    stored = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert stored["build"]["duration_seconds"] >= 0
    assert stored["build"]["size_bytes"] > 0
    assert "peak_rss_bytes" in stored["build"]
    assert report["counts"]["connections"] == 2


def _snapshot_request(algorithm="astar", include_alternatives=False, timeout=30.0):
    return {
        "origin_stop_id": "A",
        "destination_stop_id": "C",
        "departure_time": "08:00:00",
        "algorithm": algorithm,
        "include_alternatives": include_alternatives,
        "minimum_samples": 20,
        "max_extra_minutes": 30,
        "search_timeout_seconds": timeout,
        "reliability_effect": 0.5,
        "travel_time_effect": 0.5,
        "transfer_effect": 0,
    }


@pytest.mark.parametrize("algorithm", ["dijkstra", "astar"])
def test_every_public_algorithm_runs_against_real_snapshot(
    tmp_path, monkeypatch, algorithm
):
    path = tmp_path / algorithm
    build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(path))
    services = create_services()
    app.state.services = services
    app.state.owns_services = False
    try:
        with TestClient(app) as client:
            response = client.post("/routes/plan", json=_snapshot_request(algorithm))
        assert response.status_code == 200
        body = response.json()
        assert len(body["alternatives"]) == 1
        assert set(body["alternatives"][0]["fallback_levels"]) == {"default"}
        assert body["alternatives"][0]["insufficient_data"] is True
    finally:
        app.state.services = None
        services.close()


@pytest.mark.parametrize("include_alternatives", [False, True])
def test_snapshot_timeout_is_http_504_with_real_planner(
    tmp_path, monkeypatch, include_alternatives
):
    import src.routing.snapshot_search as search_module

    path = tmp_path / f"timeout-{include_alternatives}"
    build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(path))
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(search_module, "perf_counter", lambda: next(ticks, 1.0))
    services = create_services()
    app.state.services = services
    app.state.owns_services = False
    try:
        with TestClient(app) as client:
            response = client.post(
                "/routes/plan",
                json=_snapshot_request(
                    include_alternatives=include_alternatives, timeout=0.5
                ),
            )
        assert response.status_code == 504
        assert (
            response.json()["detail"]
            == "Route planning exceeded the configured timeout."
        )
    finally:
        app.state.services = None
        services.close()


def test_completed_empty_snapshot_search_is_normal_response(tmp_path, monkeypatch):
    path = tmp_path / "no-route"
    build_snapshot_from_rows(path, stops=STOPS, connections=CONNECTIONS)
    monkeypatch.setenv("ROUTING_SNAPSHOT_PATH", str(path))
    services = create_services()
    app.state.services = services
    app.state.owns_services = False
    try:
        with TestClient(app) as client:
            payload = _snapshot_request()
            payload["departure_time"] = "26:00:00"
            response = client.post("/routes/plan", json=payload)
        assert response.status_code == 200
        assert response.json()["alternatives"] == []
    finally:
        app.state.services = None
        services.close()
