"""Replaceable application services and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

import os
from pathlib import Path

from src.reliability.database import ReliabilityDatabase
from src.routing.database import TransitDatabase
from src.routing.planner import TransitPlanner
from src.routing.cache import RoutingCacheManager
from src.routing.warmup import RoutingWarmupCoordinator
from src.routing.snapshot import RoutingSnapshot, SnapshotError, SnapshotPlanner


@dataclass
class ApiServices:
    transit_database: Any
    planner: Any
    reliability_database: Any
    cache_manager: RoutingCacheManager | None = None
    warmup: RoutingWarmupCoordinator | None = None
    snapshot: RoutingSnapshot | None = None

    def close(self) -> None:
        try:
            close = getattr(self.reliability_database, "close", None)
            if close:
                close()
        finally:
            close = getattr(self.transit_database, "close", None)
            if close:
                close()


class ServicesUnavailable(RuntimeError):
    """Raised when startup could not initialize database-backed services."""


def create_services() -> ApiServices:
    snapshot_path = os.getenv("ROUTING_SNAPSHOT_PATH", "data/routing_snapshot")
    required = os.getenv("ROUTING_SNAPSHOT_REQUIRED", "true").lower() in {"1", "true", "yes", "on"}
    development_fallback = os.getenv("ROUTING_SNAPSHOT_DEVELOPMENT_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}
    try:
        snapshot = RoutingSnapshot(Path(snapshot_path))
        # Reliability profiles are part of the artifact in future formats. A
        # null resolver keeps current response semantics without opening SQL.
        return ApiServices(snapshot, SnapshotPlanner(snapshot), None, snapshot=snapshot)
    except SnapshotError:
        if required or not development_fallback:
            raise
        # Explicitly opted-in local compatibility path only.
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
