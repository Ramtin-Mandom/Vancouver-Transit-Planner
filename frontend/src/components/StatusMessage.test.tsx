import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { PlanningStatus } from "./StatusMessage";

it("renders planning loading state", () => {
  render(<PlanningStatus loading error={null} />);
  expect(screen.getByText("Comparing scheduled routes")).toBeVisible();
});

it("renders a safe API error", () => {
  render(
    <PlanningStatus loading={false} error="The transit database is temporarily unavailable." />
  );
  expect(screen.getByRole("alert")).toHaveTextContent("temporarily unavailable");
});
