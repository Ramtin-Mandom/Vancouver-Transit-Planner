"""GTFS service-calendar evaluation."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any, Protocol


class CalendarRepository(Protocol):
    def calendar_rule(self, service_id: str) -> dict[str, Any] | None: ...

    def calendar_exception(
        self, service_id: str, service_date: date
    ) -> int | None: ...


class ServiceCalendar:
    def __init__(self, database: CalendarRepository) -> None:
        self.database = database

    @lru_cache(maxsize=4096)
    def operates(self, service_id: str, service_date: date) -> bool:
        exception = self.database.calendar_exception(service_id, service_date)
        if exception == 1:
            return True
        if exception == 2:
            return False

        rule = self.database.calendar_rule(service_id)
        if not rule:
            return False
        if not rule["start_date"] <= service_date <= rule["end_date"]:
            return False
        weekday = (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )[service_date.weekday()]
        return bool(rule[weekday])
