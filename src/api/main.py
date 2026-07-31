"""FastAPI application exposing existing transit-planning services."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.data_ingestion.config import ConfigurationError
from src.reliability.profiles import ProfileResolver
from src.routing.reliable import ReliableSearchTimeout

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
    if getattr(app.state, "services", None) is None:
        try:
            app.state.services = create_services()
            app.state.owns_services = True
        except (ConfigurationError, psycopg.Error, OSError):
            logger.error("API database service initialization failed")
            app.state.services = None
            app.state.owns_services = False
    try:
        yield
    finally:
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
        content={"detail": "Database services are currently unavailable."},
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
async def timeout_handler(
    request: Request, exc: ReliableSearchTimeout
) -> JSONResponse:
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
    logger.error("Unhandled API error")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal error occurred."},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stops/search", response_model=list[StopResponse])
def search_stops(
    query: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
    services: ApiServices = Depends(get_services),
) -> list[StopResponse]:
    trimmed_query = query.strip()
    if len(trimmed_query) < 2:
        raise HTTPException(
            status_code=422,
            detail="Search query must contain at least 2 non-whitespace characters.",
        )
    return [
        serialize_stop(stop)
        for stop in services.transit_database.search_stops(
            trimmed_query, limit=limit
        )
    ]


@app.post("/routes/plan", response_model=RoutePlanResponse)
def plan_routes(
    request: RoutePlanRequest,
    services: ApiServices = Depends(get_services),
) -> RoutePlanResponse:
    origin = services.transit_database.find_stop(request.origin_stop_id)
    if origin is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown origin stop_id: {request.origin_stop_id}",
        )
    destination = services.transit_database.find_stop(
        request.destination_stop_id
    )
    if destination is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown destination stop_id: {request.destination_stop_id}",
        )

    departure_time = request.parsed_departure_time()
    resolver = ProfileResolver(
        services.reliability_database,
        minimum_samples=request.minimum_samples,
    )
    result = services.planner.get_ranked_route_result(
        request.origin_stop_id,
        request.destination_stop_id,
        request.service_date,
        departure_time,
        resolver,
        route_number=request.route_number,
        preferences=request.routing_preferences(),
        max_extra_minutes=request.max_extra_minutes,
        timeout_seconds=request.search_timeout_seconds,
        include_diagnostics=request.include_diagnostics,
    )
    return serialize_result(
        result,
        origin,
        destination,
        request.service_date,
        departure_time,
    )
