import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, checkReady, planRoutes } from "./api/client";
import { routeResult } from "./test/fixtures";

vi.mock("./api/client", async (original) => {
  const actual = await original<typeof import("./api/client")>();
  return { ...actual, checkReady: vi.fn(), planRoutes: vi.fn() };
});
vi.mock("./components/TripPlannerForm", () => ({
  TripPlannerForm: ({ onSubmit }: { onSubmit: (request: never) => void }) => (
    <button onClick={() => onSubmit({} as never)}>submit trip</button>
  )
}));
vi.mock("./components/TransitMap", () => ({ TransitMap: () => <div>map</div> }));
vi.mock("./components/RouteResults", () => ({ RouteResults: () => <div>route results</div> }));

import App from "./App";

beforeEach(() => {
  vi.mocked(checkReady).mockResolvedValue({ ready: true });
  vi.mocked(planRoutes).mockReset();
});

it("clears stale results when a newer request starts and fails", async () => {
  vi.mocked(planRoutes).mockResolvedValueOnce(routeResult);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "submit trip" }));
  expect(await screen.findByText("route results")).toBeVisible();

  let rejectNewest!: (reason: unknown) => void;
  vi.mocked(planRoutes).mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        rejectNewest = reject;
      })
  );
  fireEvent.click(screen.getByRole("button", { name: "submit trip" }));
  expect(screen.queryByText("route results")).not.toBeInTheDocument();
  await act(async () => rejectNewest(new ApiError("expired", 503, "feed_expired")));
  expect(screen.queryByText("route results")).not.toBeInTheDocument();
  expect(screen.getByText(/schedule data has expired/i)).toBeVisible();
});
