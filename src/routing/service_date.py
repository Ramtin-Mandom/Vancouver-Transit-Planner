"""Resolve the internal GTFS scheduling date in Vancouver local time."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

VANCOUVER_TIMEZONE = ZoneInfo("America/Vancouver")


def current_service_date(now: datetime | None = None) -> date:
    """Return today's GTFS service date in ``America/Vancouver``.

    ``now`` is injectable so tests and internal callers can remain deterministic.
    Naive values are rejected because interpreting them would reintroduce an
    implicit server-timezone dependency.
    """
    instant = now or datetime.now(VANCOUVER_TIMEZONE)
    if instant.tzinfo is None:
        raise ValueError("service-date clock must be timezone-aware")
    return instant.astimezone(VANCOUVER_TIMEZONE).date()
