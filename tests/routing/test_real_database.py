"""Opt-in checks against the PostgreSQL database configured by .env."""

import os

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


@pytest.mark.skipif(
    not database_environment_available(),
    reason="real PostgreSQL integration test requires all five DB_* variables",
)
def test_real_gtfs_journeys():
    from src.routing.database import TransitDatabase
    from src.routing.integration_runner import run_cases
    from src.routing.planner import TransitPlanner

    database = TransitDatabase()
    _, summary = run_cases(database, TransitPlanner(database), limit=10)
    assert summary.executed == 10
    assert summary.failed == 0
    assert summary.skipped == 0
