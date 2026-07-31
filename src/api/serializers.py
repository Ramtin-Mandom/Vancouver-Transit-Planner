"""Conversion from routing-domain dataclasses to API response models."""

from __future__ import annotations

from datetime import date, timedelta

from src.routing.cli import format_gtfs_time
from src.routing.models import ReliableSearchResult, Stop

from .schemas import (
    RouteAlternativeResponse,
    LegStopResponse,
    RouteLegResponse,
    RoutePlanResponse,
    SearchTimingResponse,
    StopResponse,
)


def serialize_stop(stop: Stop) -> StopResponse:
    return StopResponse(
        stop_id=stop.stop_id,
        stop_code=stop.stop_code,
        stop_name=stop.stop_name,
        latitude=float(stop.stop_lat) if stop.stop_lat is not None else None,
        longitude=float(stop.stop_lon) if stop.stop_lon is not None else None,
    )


def serialize_result(
    result: ReliableSearchResult,
    origin: Stop,
    destination: Stop,
    service_date: date,
    requested_departure_time: timedelta,
) -> RoutePlanResponse:
    alternatives = []
    for rank, alternative in enumerate(result.alternatives, start=1):
        itinerary = alternative.itinerary
        legs = [
            RouteLegResponse(
                trip_id=leg.trip_id,
                route_id=leg.route_id,
                route_name=leg.route_name,
                direction_id=leg.direction_id,
                origin=serialize_stop(leg.origin),
                destination=serialize_stop(leg.destination),
                departure_time=format_gtfs_time(leg.departure_time),
                arrival_time=format_gtfs_time(leg.arrival_time),
                duration_seconds=int(
                    (leg.arrival_time - leg.departure_time).total_seconds()
                ),
                stops=[
                    LegStopResponse(
                        stop=serialize_stop(item.stop),
                        stop_sequence=item.stop_sequence,
                        arrival_time=(
                            format_gtfs_time(item.arrival_time)
                            if item.arrival_time is not None
                            else None
                        ),
                        departure_time=(
                            format_gtfs_time(item.departure_time)
                            if item.departure_time is not None
                            else None
                        ),
                    )
                    for item in leg.stops
                ],
            )
            for leg in itinerary.legs
        ]
        duration = itinerary.total_scheduled_travel_time
        alternatives.append(
            RouteAlternativeResponse(
                rank=rank,
                departure_time=format_gtfs_time(itinerary.departure_time),
                arrival_time=format_gtfs_time(itinerary.arrival_time),
                duration_seconds=int(duration.total_seconds()),
                duration_display=format_gtfs_time(duration),
                transfer_count=itinerary.transfer_count,
                route_reliability=alternative.route_reliability,
                combined_score=alternative.combined_score,
                speed_component=alternative.speed_component,
                fallback_levels=list(alternative.fallback_levels),
                insufficient_data=alternative.insufficient_data,
                legs=legs,
            )
        )
    timing = result.timing
    return RoutePlanResponse(
        origin=serialize_stop(origin),
        destination=serialize_stop(destination),
        service_date=service_date,
        requested_departure_time=format_gtfs_time(requested_departure_time),
        alternatives=alternatives,
        timing=SearchTimingResponse(
            data_loading_ms=timing.data_loading_ms,
            search_ms=timing.search_ms,
            ranking_ms=timing.ranking_ms,
            total_ms=timing.total_ms,
        ),
    )
