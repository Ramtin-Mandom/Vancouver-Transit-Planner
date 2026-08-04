import { afterEach, describe, expect, it, vi } from "vitest";
import { planRoutes, searchStops } from "./client";
import type { RoutePlanRequest } from "./types";

afterEach(() => vi.unstubAllGlobals());

describe("API client", () => {
  it("constructs an encoded stop-search request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => []
    });
    vi.stubGlobal("fetch", fetchMock);
    await searchStops("  Main & 5th  ", 7);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/stops/search?query=Main+%26+5th&limit=7",
      { signal: undefined }
    );
  });

  it("sends the complete successful route-plan request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ alternatives: [] })
    });
    vi.stubGlobal("fetch", fetchMock);
    const payload: RoutePlanRequest = {
      origin_stop_id: "646",
      destination_stop_id: "31",
      departure_time: "25:10:00",
      include_alternatives: false,
      minimum_samples: 20,
      max_extra_minutes: 30,
      search_timeout_seconds: 30,
      reliability_effect: 0.5,
      travel_time_effect: 0.5,
      transfer_effect: 0
    };
    await planRoutes(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/routes/plan",
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) })
    );
  });

  it("parses FastAPI validation arrays without exposing internals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({
          detail: [{ loc: ["body", "include_alternatives"], msg: "Input should be a valid boolean" }]
        })
      })
    );
    await expect(searchStops("Main")).rejects.toThrow(
      "include_alternatives: Input should be a valid boolean"
    );
  });
});
