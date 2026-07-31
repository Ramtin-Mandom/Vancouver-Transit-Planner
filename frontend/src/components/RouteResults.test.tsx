import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { routeResult } from "../test/fixtures";
import { RouteResults } from "./RouteResults";

describe("RouteResults", () => {
  it("preserves backend order and derives fastest/reliable labels", () => {
    render(<RouteResults result={routeResult} />);
    const cards = screen.getAllByRole("article");
    expect(within(cards[0]).getByText("5 → 99 B-Line")).toBeVisible();
    expect(within(cards[1]).getByText("14")).toBeVisible();
    expect(within(cards[0]).getByText("Most reliable")).toBeVisible();
    expect(within(cards[1]).getByText("Fastest")).toBeVisible();
    expect(within(cards[0]).getByText("Best overall")).toBeVisible();
  });

  it("renders a multi-leg itinerary and GTFS times beyond 24:00", async () => {
    render(<RouteResults result={routeResult} />);
    const technicalDetails = screen.getAllByText("Technical details");
    await userEvent.click(technicalDetails[0]);
    await userEvent.click(technicalDetails[1]);
    expect(screen.getByText("TRIP-A")).toBeVisible();
    expect(screen.getByText("TRIP-B")).toBeVisible();
    expect(screen.getAllByText("25:12:00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("26:00:00").length).toBeGreaterThan(0);
    expect(screen.getByText("Transfer at Broadway Station")).toBeVisible();
  });

  it("shows an insufficient-data warning", () => {
    render(<RouteResults result={routeResult} />);
    expect(screen.getByText(/Limited historical data/)).toBeVisible();
  });

  it("expands and collapses ordered leg stops independently", async () => {
    render(<RouteResults result={routeResult} />);
    const first = screen.getByRole("button", { name: "View 2 intermediate stops" });
    const second = screen.getByRole("button", { name: "View 1 intermediate stop" });

    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Davie Street")).not.toBeInTheDocument();
    await userEvent.click(first);
    expect(first).toHaveAttribute("aria-expanded", "true");
    const firstList = screen.getByRole("list", { name: "5 scheduled stops" });
    expect(within(firstList).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "25:12:00Granville Station",
      "25:20:00Davie Street",
      "25:26:00City Hall",
      "25:32:00Broadway Station"
    ]);
    expect(second).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(second);
    expect(screen.getByText("Alma Street")).toBeVisible();
    expect(first).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(first);
    expect(screen.queryByText("Davie Street")).not.toBeInTheDocument();
  });

  it("does not show a disclosure for a leg without intermediate stops", async () => {
    render(<RouteResults result={routeResult} />);
    await userEvent.click(screen.getByRole("button", { name: "View itinerary" }));
    expect(screen.getAllByRole("button", { name: /intermediate/ })).toHaveLength(2);
  });

  it("expands non-primary route details with a button", async () => {
    render(<RouteResults result={routeResult} />);
    const buttons = screen.getAllByRole("button", { name: "View itinerary" });
    await userEvent.click(buttons[0]);
    const technicalDetails = screen.getAllByText("Technical details");
    await userEvent.click(technicalDetails.at(-1)!);
    expect(screen.getByText("TRIP-FAST")).toBeVisible();
  });

  it("renders a helpful no-route state", () => {
    render(
      <RouteResults result={{ ...routeResult, alternatives: [] }} />
    );
    expect(screen.getByText("No scheduled routes found")).toBeVisible();
  });
});
