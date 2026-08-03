export interface Stop {
  stop_id: string;
  stop_code: string | null;
  stop_name: string;
  latitude: number | null;
  longitude: number | null;
}

export interface RouteLeg {
  trip_id: string;
  route_id: string;
  route_name: string;
  direction_id: number | null;
  origin: Stop;
  destination: Stop;
  departure_time: string;
  arrival_time: string;
  duration_seconds: number;
  stops: LegStop[];
}

export interface LegStop {
  stop: Stop;
  stop_sequence: number;
  arrival_time: string | null;
  departure_time: string | null;
}

export interface RouteAlternative {
  rank: number;
  departure_time: string;
  arrival_time: string;
  duration_seconds: number;
  duration_display: string;
  transfer_count: number;
  route_reliability: number;
  combined_score: number;
  speed_component: number;
  fallback_levels: string[];
  insufficient_data: boolean;
  legs: RouteLeg[];
}

export interface SearchTiming {
  data_loading_ms: number;
  search_ms: number;
  ranking_ms: number;
  total_ms: number;
}

export interface RoutePlanResponse {
  origin: Stop;
  destination: Stop;
  service_date: string;
  requested_departure_time: string;
  alternatives: RouteAlternative[];
  timing: SearchTiming;
}

export interface RoutePlanRequest {
  origin_stop_id: string;
  destination_stop_id: string;
  departure_time: string;
  algorithm?: "baseline" | "dijkstra" | "astar" | "mc_raptor";
  route_number: number;
  minimum_samples: number;
  max_extra_minutes: number;
  search_timeout_seconds: number;
  reliability_effect: number;
  travel_time_effect: number;
  transfer_effect: number;
}

export type ApiStatus = "checking" | "connected" | "unavailable";
