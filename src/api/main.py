"""FastAPI application exposing existing transit-planning services."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import date
from time import perf_counter
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.data_ingestion.config import ConfigurationError
from src.reliability.profiles import ProfileResolver
from src.routing.reliable import ReliableSearchTimeout
from src.routing.service_date import current_service_date
from src.routing.snapshot import MAX_ALTERNATIVES, SnapshotError

from .dependencies import (
    ApiServices,
    ServicesUnavailable,
    create_services,
    get_services,
)
from .schemas import RoutePlanRequest, RoutePlanResponse, StopResponse
from .serializers import serialize_result, serialize_stop

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def configured_cors_origins() -> list[str]:
    configured = os.getenv("API_CORS_ORIGINS")
    if not configured:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.snapshot_failure = None
    if getattr(app.state, "services", None) is None:
        started = perf_counter()
        try:
            app.state.services = create_services()
            app.state.owns_services = True
            snapshot = app.state.services.snapshot
            if snapshot is not None:
                logger.info(
                    "Routing snapshot loaded",
                    extra={
                        "load_ms": (perf_counter() - started) * 1000,
                        "counts": snapshot.manifest["counts"],
                    },
                )
        except (ConfigurationError, psycopg.Error, OSError, SnapshotError) as exc:
            logger.error("API routing service initialization failed")
            app.state.snapshot_failure = str(exc)[:240]
            app.state.services = None
            app.state.owns_services = False
    warmup_task = None
    services = getattr(app.state, "services", None)
    if services is not None and services.warmup is not None:
        coordinator = services.warmup

        async def run_essential() -> None:
            try:
                await asyncio.to_thread(coordinator.warm_essential)
                if coordinator.configuration.tomorrow_index:
                    app.state.background_warmup_task = asyncio.create_task(
                        asyncio.to_thread(coordinator.warm_tomorrow)
                    )
            except Exception:
                # The coordinator records the failing phase; /ready remains
                # false and a later process/request can retry safely.
                logger.error("API cache warm-up did not complete")

        if coordinator.configuration.enabled:
            warmup_task = asyncio.create_task(run_essential())
            app.state.warmup_task = warmup_task
            if coordinator.configuration.block_readiness:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(warmup_task),
                        timeout=coordinator.configuration.timeout_seconds,
                    )
                except TimeoutError:
                    logger.warning(
                        "API cache warm-up continues after readiness timeout"
                    )
    try:
        yield
    finally:
        services = getattr(app.state, "services", None)
        if services is not None and services.warmup is not None:
            services.warmup.request_stop()
        for task_name in ("background_warmup_task", "warmup_task"):
            task = getattr(app.state, task_name, None)
            if task is not None and not task.done():
                with suppress(Exception):
                    await task
            setattr(app.state, task_name, None)
        services = getattr(app.state, "services", None)
        if services is not None and getattr(app.state, "owns_services", False):
            try:
                services.close()
            except Exception:
                logger.error("API service cleanup failed")
            finally:
                app.state.services = None


app = FastAPI(
    title="Vancouver Transit Planner API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(ServicesUnavailable)
async def unavailable_handler(
    request: Request, exc: ServicesUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc) or "Routing services are currently unavailable."},
    )


@app.exception_handler(ConfigurationError)
@app.exception_handler(psycopg.Error)
@app.exception_handler(OSError)
async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Database operation failed")
    return JSONResponse(
        status_code=503,
        content={"detail": "Database services are currently unavailable."},
    )


@app.exception_handler(ReliableSearchTimeout)
async def timeout_handler(request: Request, exc: ReliableSearchTimeout) -> JSONResponse:
    if not exc.log_context:
        logger.warning("Route search timed out")
    content = {"detail": "Route planning exceeded the configured timeout."}
    if exc.diagnostics is not None:
        from .serializers import serialize_diagnostics

        content["diagnostics"] = serialize_diagnostics(exc.diagnostics).model_dump()
    return JSONResponse(
        status_code=504,
        content=content,
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal error occurred."},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(request: Request) -> JSONResponse:
    services = getattr(request.app.state, "services", None)
    if services is None:
        content = {
            "ready": False,
            "snapshot_loaded": False,
            "reason": getattr(request.app.state, "snapshot_failure", None)
            or "routing services unavailable",
        }
        return JSONResponse(status_code=503, content=content)
    if services.routing_unavailable_reason is not None:
        return JSONResponse(
            status_code=503,
            content={
                "ready": False,
                "snapshot_loaded": False,
                "autocomplete_available": True,
                "reason": services.routing_unavailable_reason,
            },
        )
    if services.snapshot is not None:
        manifest = services.snapshot.manifest
        service_range = manifest.get("service_range", {})
        latest = service_range.get("latest_date")
        today = current_service_date()
        if latest and today > date.fromisoformat(latest):
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "snapshot_loaded": True,
                    "reason": f"GTFS feed expired on {latest}; refresh the feed and rebuild the snapshot",
                    "service_range": service_range,
                },
            )
        warning = None
        if latest and (date.fromisoformat(latest) - today).days <= 30:
            warning = f"GTFS feed expires on {latest}"
        return JSONResponse(
            status_code=200,
            content={
                "ready": True,
                "snapshot_loaded": True,
                "snapshot_version": manifest["format_version"],
                "source_version": manifest.get("source_version"),
                "counts": manifest["counts"],
                "service_range": service_range,
                "warning": warning,
            },
        )
    if services.warmup is None:
        content = {
            "ready": True,
            "gtfs_version": None,
            "essential_warmup_complete": True,
            "skytrain_warmup_complete": False,
            "background_warmup_running": False,
        }
        return JSONResponse(status_code=200, content=content)
    state = services.warmup.state()
    content = {
        "ready": state.ready,
        "gtfs_version": state.gtfs_version,
        "essential_warmup_complete": state.essential_warmup_complete,
        "skytrain_warmup_complete": state.skytrain_warmup_complete,
        "background_warmup_running": state.background_warmup_running,
        "warmup_started": state.warmup_started,
        "warmup_complete": state.warmup_complete,
        "warmup_failed": state.warmup_failed,
        "warmup_total_ms": state.warmup_total_ms,
        "static_snapshot_warmup_ms": state.static_snapshot_warmup_ms,
        "daily_index_warmup_ms": state.daily_index_warmup_ms,
        "skytrain_warmup_ms": state.skytrain_warmup_ms,
        "reliability_warmup_ms": state.reliability_warmup_ms,
        "skytrain_routes_found": state.skytrain_routes_found,
        "skytrain_active_trips": state.skytrain_active_trips,
        "skytrain_connections_loaded": state.skytrain_connections_loaded,
        "skytrain_cache_entries": state.skytrain_cache_entries,
        "skytrain_cache_memory_bytes": state.skytrain_cache_memory_bytes,
        "single_flight_wait_count": services.cache_manager.single_flight_wait_count
        if services.cache_manager is not None
        else 0,
    }
    return JSONResponse(status_code=200 if state.ready else 503, content=content)


@app.get("/stops/search", response_model=list[StopResponse])
def search_stops(
    services: Annotated[ApiServices, Depends(get_services)],
    query: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
) -> list[StopResponse]:
    trimmed_query = query.strip()
    if len(trimmed_query) < 2:
        raise HTTPException(
            status_code=422,
            detail="Search query must contain at least 2 non-whitespace characters.",
        )
    return [
        serialize_stop(stop)
        for stop in services.transit_database.search_stops(trimmed_query, limit=limit)
    ]


@app.post("/routes/plan", response_model=RoutePlanResponse)
def plan_routes(
    request: RoutePlanRequest,
    services: Annotated[ApiServices, Depends(get_services)],
) -> RoutePlanResponse:
    if services.planner is None:
        raise ServicesUnavailable(
            services.routing_unavailable_reason or "routing services are unavailable"
        )
    # The public API does not expose future-date planning yet. Timetable logic
    # still receives an internal Vancouver-local GTFS service date.
    service_date = current_service_date()
    if services.snapshot is not None:
        latest = services.snapshot.manifest.get("service_range", {}).get("latest_date")
        if latest and service_date > date.fromisoformat(latest):
            raise ServicesUnavailable(
                f"GTFS feed expired on {latest}; refresh the feed and rebuild the snapshot"
            )
    origin = services.transit_database.find_stop(request.origin_stop_id)
    if origin is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown origin stop_id: {request.origin_stop_id}",
        )
    destination = services.transit_database.find_stop(request.destination_stop_id)
    if destination is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown destination stop_id: {request.destination_stop_id}",
        )

    departure_time = request.parsed_departure_time()
    resolver = None
    if services.reliability_database is not None:
        profile_version_lookup = getattr(
            services.reliability_database, "profile_version", None
        )
        profile_version = (
            str(profile_version_lookup())
            if callable(profile_version_lookup)
            else "unknown"
        )
        resolver = ProfileResolver(
            services.reliability_database,
            minimum_samples=request.minimum_samples,
            shared_cache=(
                services.cache_manager if request.cache_mode == "shared" else None
            ),
            profile_version=profile_version,
        )
    result = services.planner.get_ranked_route_result(
        request.origin_stop_id,
        request.destination_stop_id,
        service_date,
        departure_time,
        resolver,
        algorithm=request.algorithm,
        cache_mode=request.cache_mode,
        minimum_samples=request.minimum_samples,
        route_number=(MAX_ALTERNATIVES if request.include_alternatives else 1),
        include_alternatives=request.include_alternatives,
        preferences=request.routing_preferences(),
        max_extra_minutes=request.max_extra_minutes,
        timeout_seconds=request.search_timeout_seconds,
        include_diagnostics=request.include_diagnostics,
    )
    # Defense in depth: the public endpoint never serializes more than three
    # candidates, even if a custom planner ignores route_number.
    public_limit = MAX_ALTERNATIVES if request.include_alternatives else 1
    result = replace(result, alternatives=result.alternatives[:public_limit])
    return serialize_result(
        result,
        origin,
        destination,
        service_date,
        departure_time,
    )
