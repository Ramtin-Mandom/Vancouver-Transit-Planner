import type { RoutePlanRequest, RoutePlanResponse, Stop } from "./types";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ??
  "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ValidationIssue {
  loc?: Array<string | number>;
  msg?: string;
}

function errorMessage(status: number, payload: unknown): string {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "detail" in payload
  ) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((issue: ValidationIssue) => {
          const field = issue.loc?.at(-1);
          return `${field ? `${String(field)}: ` : ""}${issue.msg ?? "Invalid value"}`;
        })
        .join(" ");
    }
  }
  const defaults: Record<number, string> = {
    404: "One of the selected stops could not be found.",
    422: "Please review the trip details and try again.",
    503: "The transit database is temporarily unavailable.",
    504: "Route planning took too long. Try a narrower search."
  };
  return defaults[status] ?? "Something went wrong while contacting the planner.";
}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Unable to reach the planner API. Is FastAPI running?");
  }
  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new ApiError(errorMessage(response.status, payload), response.status);
  }
  return response.json() as Promise<T>;
}

export function checkHealth(signal?: AbortSignal): Promise<{ status: "ok" }> {
  return request("/health", { signal });
}

export function searchStops(
  query: string,
  limit = 10,
  signal?: AbortSignal
): Promise<Stop[]> {
  const params = new URLSearchParams({ query: query.trim(), limit: String(limit) });
  return request(`/stops/search?${params.toString()}`, { signal });
}

export function planRoutes(
  payload: RoutePlanRequest,
  signal?: AbortSignal
): Promise<RoutePlanResponse> {
  return request("/routes/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal
  });
}
