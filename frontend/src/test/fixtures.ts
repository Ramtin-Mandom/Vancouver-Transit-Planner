import type { RoutePlanResponse, Stop } from "../api/types";

export const origin: Stop = {
  stop_id: "646",
  stop_code: "50001",
  stop_name: "Granville Station",
  latitude: 49.283,
  longitude: -123.117
};

export const destination: Stop = {
  stop_id: "31",
  stop_code: "60001",
  stop_name: "UBC Exchange",
  latitude: 49.267,
  longitude: -123.247
};

export const routeResult: RoutePlanResponse = {
  origin,
  destination,
  service_date: "2026-07-29",
  requested_departure_time: "25:10:00",
  alternatives: [
    {
      rank: 1,
      departure_time: "25:12:00",
      arrival_time: "26:00:00",
      duration_seconds: 2880,
      duration_display: "00:48:00",
      transfer_count: 1,
      route_reliability: 0.82,
      combined_score: 88.4,
      speed_component: 0.88,
      fallback_levels: ["route_direction_window", "route"],
      insufficient_data: true,
      legs: [
        {
          trip_id: "TRIP-A",
          route_id: "R5",
          route_name: "5",
          direction_id: 0,
          origin,
          destination: { ...origin, stop_id: "100", stop_name: "Broadway Station" },
          departure_time: "25:12:00",
          arrival_time: "25:32:00",
          duration_seconds: 1200
        },
        {
          trip_id: "TRIP-B",
          route_id: "R99",
          route_name: "99 B-Line",
          direction_id: 1,
          origin: { ...origin, stop_id: "100", stop_name: "Broadway Station" },
          destination,
          departure_time: "25:38:00",
          arrival_time: "26:00:00",
          duration_seconds: 1320
        }
      ]
    },
    {
      rank: 2,
      departure_time: "25:15:00",
      arrival_time: "25:55:00",
      duration_seconds: 2400,
      duration_display: "00:40:00",
      transfer_count: 0,
      route_reliability: 0.7,
      combined_score: 84.1,
      speed_component: 1,
      fallback_levels: ["route_direction_window"],
      insufficient_data: false,
      legs: [
        {
          trip_id: "TRIP-FAST",
          route_id: "R14",
          route_name: "14",
          direction_id: 0,
          origin,
          destination,
          departure_time: "25:15:00",
          arrival_time: "25:55:00",
          duration_seconds: 2400
        }
      ]
    }
  ],
  timing: {
    data_loading_ms: 1,
    search_ms: 12.4,
    ranking_ms: 0.8,
    total_ms: 14.2
  }
};
