"""Hierarchical reliability-profile lookup."""

from __future__ import annotations

from .models import ProfileSelection

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

    def resolve(self, route_id, stop_id, weekday, hour) -> ProfileSelection:
        values = (route_id, stop_id, weekday, hour)
        for level, use_route, use_stop, use_weekday, use_hour in FALLBACKS:
            profile = self.database.profile(
                values[0] if use_route else None,
                values[1] if use_stop else None,
                values[2] if use_weekday else None,
                values[3] if use_hour else None,
            )
            if profile and profile.sample_count >= self.minimum_samples:
                return ProfileSelection(profile, level, False)
        return ProfileSelection(None, "insufficient-data", True)
