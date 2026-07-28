"""Hierarchical reliability-profile lookup."""

from __future__ import annotations

from .models import ProfileSelection, ReliabilityProfile

FALLBACKS = (
    ("exact", True, True, True, True),
    ("route-stop-hour", True, True, False, True),
    ("route-weekday-hour", True, False, True, True),
    ("route-hour", True, False, False, True),
    ("route", True, False, False, False),
    ("system", False, False, False, False),
)


class ProfileResolver:
    def __init__(self, database, minimum_samples: int = 20) -> None:
        self.database = database
        self.minimum_samples = minimum_samples
        self._cache = {}
        self._route_rows = {}

    def set_statement_timeout(self, milliseconds: int) -> None:
        configure = getattr(self.database, "set_statement_timeout", None)
        if callable(configure):
            configure(milliseconds)

    def resolve(self, route_id, stop_id, weekday, hour) -> ProfileSelection:
        key = (route_id, stop_id, weekday, hour)
        if key in self._cache:
            return self._cache[key]
        values = (route_id, stop_id, weekday, hour)
        for level, use_route, use_stop, use_weekday, use_hour in FALLBACKS:
            route_profiles = getattr(self.database, "route_profiles", None)
            if use_route and callable(route_profiles):
                if route_id not in self._route_rows:
                    self._route_rows[route_id] = route_profiles(route_id)
                rows = [
                    row for row in self._route_rows[route_id]
                    if (not use_stop or row["stop_id"] == stop_id)
                    and (not use_weekday or row["weekday"] == weekday)
                    and (not use_hour or row["hour_of_day"] == hour)
                ]
                profile = self._aggregate_rows(
                    rows,
                    route_id,
                    stop_id if use_stop else None,
                    weekday if use_weekday else None,
                    hour if use_hour else None,
                )
            else:
                profile = self.database.profile(
                    values[0] if use_route else None,
                    values[1] if use_stop else None,
                    values[2] if use_weekday else None,
                    values[3] if use_hour else None,
                )
            if profile and profile.sample_count >= self.minimum_samples:
                selection = ProfileSelection(profile, level, False)
                self._cache[key] = selection
                return selection
        selection = ProfileSelection(None, "insufficient-data", True)
        self._cache[key] = selection
        return selection

    @staticmethod
    def _aggregate_rows(
        rows, route_id, stop_id, weekday, hour
    ) -> ReliabilityProfile | None:
        samples = sum(row["sample_count"] for row in rows)
        if samples == 0:
            return None

        def weighted(field, default=0.0):
            return sum(
                (row.get(field) if row.get(field) is not None else default)
                * row["sample_count"]
                for row in rows
            ) / samples

        return ReliabilityProfile(
            route_id=route_id,
            stop_id=stop_id,
            weekday=weekday,
            hour_of_day=hour,
            sample_count=samples,
            mean_delay_seconds=weighted("mean_delay_seconds"),
            mean_absolute_delay_seconds=weighted(
                "mean_absolute_delay_seconds"
            ),
            delay_stddev_seconds=weighted("delay_stddev_seconds"),
            p50_delay_seconds=weighted("p50_delay_seconds"),
            p90_delay_seconds=weighted("p90_delay_seconds"),
            early_probability=weighted("early_probability"),
            on_time_probability=weighted("on_time_probability"),
            late_probability=weighted("late_probability"),
        )
