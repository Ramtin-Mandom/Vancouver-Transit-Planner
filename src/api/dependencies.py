"""Replaceable application services and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from src.reliability.database import ReliabilityDatabase
from src.routing.database import TransitDatabase
from src.routing.planner import TransitPlanner
from src.routing.cache import RoutingCacheManager
from src.routing.warmup import RoutingWarmupCoordinator


@dataclass
class ApiServices:
    transit_database: Any
    planner: Any
    reliability_database: Any
    cache_manager: RoutingCacheManager | None = None
    warmup: RoutingWarmupCoordinator | None = None

    def close(self) -> None:
        try:
            self.reliability_database.close()
        finally:
            self.transit_database.close()


class ServicesUnavailable(RuntimeError):
    """Raised when startup could not initialize database-backed services."""


def create_services() -> ApiServices:
    transit_database = TransitDatabase()
    try:
        transit_database.initialize()
        reliability_database = ReliabilityDatabase()
    except Exception:
        transit_database.close()
        raise
    cache_manager = RoutingCacheManager()
    warmup = RoutingWarmupCoordinator(
        cache_manager, transit_database, reliability_database
    )
    return ApiServices(
        transit_database=transit_database,
        planner=TransitPlanner(transit_database, cache_manager=cache_manager),
        reliability_database=reliability_database,
        cache_manager=cache_manager,
        warmup=warmup,
    )


def get_services(request: Request) -> ApiServices:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise ServicesUnavailable("API database services are unavailable")
    return services
