"""Opt-in checks against the PostgreSQL database configured by .env."""

import os
from datetime import timedelta
from time import perf_counter

import pytest
from dotenv import load_dotenv

from src.data_ingestion.config import PROJECT_ROOT

pytestmark = pytest.mark.integration


def database_environment_available():
    load_dotenv(PROJECT_ROOT / ".env")
    return all(
        os.getenv(name)
        for name in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    )


requires_database = pytest.mark.skipif(
    not database_environment_available(),
    reason="real PostgreSQL integration test requires all five DB_* variables",
)


@pytest.fixture(scope="session")
def real_database():
    if not database_environment_available():
        pytest.skip("real PostgreSQL integration test requires all five DB_* variables")
    from src.routing.database import TransitDatabase

    started = perf_counter()
    database = TransitDatabase()
    database.initialize()
    database._integration_initialization_seconds = perf_counter() - started
    yield database
    database.close()


@pytest.fixture(scope="session")
def real_planner(real_database):
    from src.routing.planner import TransitPlanner

    started = perf_counter()
    planner = TransitPlanner(real_database)
    planner._integration_construction_seconds = perf_counter() - started
    return planner


@pytest.fixture(scope="session")
def real_results(real_database, real_planner):
    from src.routing.integration_runner import run_cases

    results, summary = run_cases(
        real_database,
        real_planner,
        limit=10,
        initialization_seconds=real_database._integration_initialization_seconds,
        structure_construction_seconds=(
            real_planner._integration_construction_seconds
        ),
    )
    assert summary.executed == 10
    assert summary.failed == 0
    assert summary.skipped == 0
    return results


@requires_database
def test_real_gtfs_diverse_direct_and_transfer_journeys(real_results):
    itineraries = [result.itinerary for result in real_results]
    assert all(itinerary is not None for itinerary in itineraries)
    assert any(len(itinerary.legs) == 1 for itinerary in itineraries)
    assert any(itinerary.transfer_count >= 1 for itinerary in itineraries)
    assert len({result.case.source_route_id for result in real_results}) == 10


@requires_database
def test_real_gtfs_multiple_routes(real_database, real_results):
    assert any(
        real_database.route_count_between(
            result.case.origin_stop_id, result.case.destination_stop_id
        )
        >= 2
        for result in real_results
    )


@requires_database
def test_real_gtfs_before_and_after_departure(real_planner, real_results):
    case = real_results[0].case
    before = real_results[0].itinerary
    assert before is not None
    assert before.legs[0].departure_time >= case.departure_time - timedelta(minutes=1)

    requested_after = case.departure_time + timedelta(seconds=1)
    after = real_planner.plan(
        case.origin_stop_id,
        case.destination_stop_id,
        case.service_date,
        requested_after,
    )
    if after is not None:
        assert after.legs[0].departure_time >= requested_after


@requires_database
def test_real_gtfs_no_available_route(real_planner, real_results):
    case = real_results[0].case
    result = real_planner.plan(
        case.origin_stop_id,
        case.destination_stop_id,
        case.service_date,
        timedelta(days=7),
    )
    assert result is None


@requires_database
def test_real_gtfs_different_service_date(
    real_database, real_planner, real_results
):
    case = real_results[0].case
    next_date = real_database.next_operating_date_for_trip(
        case.source_trip_id, case.service_date
    )
    if next_date is None:
        pytest.skip("sampled trip has no later operating date")
    result = real_planner.plan(
        case.origin_stop_id,
        case.destination_stop_id,
        next_date,
        case.departure_time - timedelta(minutes=1),
    )
    assert result is not None
    assert result.service_date == next_date


@requires_database
def test_real_gtfs_after_24_hour_time_if_available(real_results):
    after_midnight = [
        result
        for result in real_results
        if result.case.departure_time >= timedelta(hours=24)
    ]
    if not after_midnight:
        pytest.skip("feed has no sampled GTFS journey after 24:00:00")
    assert all(result.itinerary is not None for result in after_midnight)


@requires_database
def test_reliability_scoring_if_implemented(real_results):
    itinerary = real_results[0].itinerary
    if itinerary is None or not hasattr(itinerary, "reliability_score"):
        pytest.skip("routing reliability scoring is not implemented")
    assert 0.0 <= itinerary.reliability_score <= 1.0
