import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { routeResult } from "../test/fixtures";
import { TransitMap } from "./TransitMap";

vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TileLayer: ({ eventHandlers }: { eventHandlers?: { tileerror?: () => void } }) => (
    <button onClick={eventHandlers?.tileerror}>simulate tile failure</button>
  ),
  Polyline: ({
    positions,
    pathOptions
  }: {
    positions: unknown[];
    pathOptions: { weight: number };
  }) => (
    <div
      data-testid="route-line"
      data-points={JSON.stringify(positions)}
      data-weight={pathOptions.weight}
    />
  ),
  CircleMarker: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Popup: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
  useMap: () => ({ setView: vi.fn(), fitBounds: vi.fn() })
}));

it("keeps alternative geometry separate and synchronizes selection", () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TransitMap result={routeResult} selectedRank={1} onSelect={onSelect} />
  );
  const lines = screen.getAllByTestId("route-line");
  expect(lines).toHaveLength(2);
  expect(lines[0]).toHaveAttribute("data-weight", "6");
  expect(lines[1]).toHaveAttribute("data-weight", "3");
  expect(JSON.parse(lines[0].getAttribute("data-points")!)).toHaveLength(6);
  expect(JSON.parse(lines[1].getAttribute("data-points")!)).toHaveLength(2);
  expect(screen.getByText("Route 1 origin: Granville Station")).toBeVisible();
  expect(screen.getByText("Route 1 destination: UBC Exchange")).toBeVisible();
  expect(screen.getByText("Route 2 origin: Granville Station")).toBeVisible();
  expect(screen.getByText("Route 2 destination: UBC Exchange")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Route 2" }));
  expect(onSelect).toHaveBeenCalledWith(2);
  rerender(<TransitMap result={routeResult} selectedRank={2} onSelect={onSelect} />);
  expect(screen.getAllByTestId("route-line")[1]).toHaveAttribute("data-weight", "6");
});

it("handles missing coordinates and tile failures gracefully", () => {
  const missing = {
    ...routeResult,
    alternatives: routeResult.alternatives.map((alternative) => ({
      ...alternative,
      legs: alternative.legs.map((leg) => ({
        ...leg,
        stops: leg.stops.map((item) => ({
          ...item,
          stop: { ...item.stop, latitude: null, longitude: null }
        })),
        origin: { ...leg.origin, latitude: null, longitude: null },
        destination: { ...leg.destination, latitude: null, longitude: null }
      }))
    }))
  };
  const { rerender } = render(
    <TransitMap result={missing} selectedRank={1} onSelect={() => undefined} />
  );
  expect(screen.getByText("Map coordinates unavailable")).toBeVisible();
  rerender(<TransitMap result={routeResult} selectedRank={1} onSelect={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "simulate tile failure" }));
  expect(screen.getByText(/Map tiles could not be loaded/)).toBeVisible();
});
