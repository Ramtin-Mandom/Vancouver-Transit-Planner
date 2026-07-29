"""API-specific request and response models."""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.routing.cli import parse_gtfs_time
from src.routing.route_results import RoutingPreferences


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StopResponse(ApiModel):
    stop_id: str
    stop_code: str | None
    stop_name: str
    latitude: float | None
    longitude: float | None


class RouteLegResponse(ApiModel):
    trip_id: str
    route_id: str
    route_name: str
    direction_id: int | None
    origin: StopResponse
    destination: StopResponse
    departure_time: str
    arrival_time: str
    duration_seconds: int


class RouteAlternativeResponse(ApiModel):
    rank: int
    departure_time: str
    arrival_time: str
    duration_seconds: int
    duration_display: str
    transfer_count: int
    route_reliability: float
    combined_score: float
    speed_component: float
    fallback_levels: list[str]
    insufficient_data: bool
    legs: list[RouteLegResponse]


class SearchTimingResponse(ApiModel):
    data_loading_ms: float
    search_ms: float
    ranking_ms: float
    total_ms: float


class RoutePlanResponse(ApiModel):
    origin: StopResponse
    destination: StopResponse
    service_date: date
    requested_departure_time: str
    alternatives: list[RouteAlternativeResponse]
    timing: SearchTimingResponse


class RoutePlanRequest(ApiModel):
    origin_stop_id: str
    destination_stop_id: str
    service_date: date
    departure_time: str
    route_number: int = Field(default=5, ge=1, le=5)
    minimum_samples: int = Field(default=20, ge=1)
    max_extra_minutes: int = Field(default=30, ge=0, le=120)
    search_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    reliability_effect: float = Field(default=0.5, ge=0)
    travel_time_effect: float = Field(default=0.5, ge=0)
    transfer_effect: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_route_request(self) -> "RoutePlanRequest":
        self.origin_stop_id = self.origin_stop_id.strip()
        self.destination_stop_id = self.destination_stop_id.strip()
        if not self.origin_stop_id or not self.destination_stop_id:
            raise ValueError("origin and destination stop IDs must not be blank")
        if self.origin_stop_id == self.destination_stop_id:
            raise ValueError("origin and destination stop IDs must be different")
        self.parsed_departure_time()
        self.routing_preferences()
        return self

    def parsed_departure_time(self) -> timedelta:
        try:
            return parse_gtfs_time(self.departure_time.strip())
        except (argparse.ArgumentTypeError, TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    def routing_preferences(self) -> RoutingPreferences:
        return RoutingPreferences(
            reliability_effect=self.reliability_effect,
            travel_time_effect=self.travel_time_effect,
            transfer_effect=self.transfer_effect,
        )
