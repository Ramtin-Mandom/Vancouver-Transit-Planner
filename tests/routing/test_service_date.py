from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.routing.service_date import current_service_date


def test_service_date_uses_vancouver_calendar_day():
    # This UTC instant is still the previous calendar day in Vancouver.
    instant = datetime(2026, 8, 3, 6, 30, tzinfo=UTC)
    assert current_service_date(instant).isoformat() == "2026-08-02"


def test_service_date_accepts_an_injectable_aware_local_clock():
    instant = datetime(2026, 8, 3, 23, 30, tzinfo=ZoneInfo("America/Vancouver"))
    assert current_service_date(instant).isoformat() == "2026-08-03"


def test_service_date_rejects_ambiguous_naive_clock():
    with pytest.raises(ValueError, match="timezone-aware"):
        current_service_date(datetime(2026, 8, 3, 12))
