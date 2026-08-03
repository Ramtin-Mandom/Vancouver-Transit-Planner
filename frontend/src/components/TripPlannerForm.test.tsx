import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { destination, origin } from "../test/fixtures";
import { TripPlannerForm } from "./TripPlannerForm";

vi.mock("./StopAutocomplete", () => ({
  StopAutocomplete: ({
    label,
    value,
    onChange,
    error
  }: {
    label: string;
    value: typeof origin | null;
    onChange: (stop: typeof origin | null) => void;
    error?: string;
  }) => (
    <div>
      <span>{label}: {value?.stop_name ?? "none"}</span>
      <button
        type="button"
        onClick={() => onChange(label === "Origin" ? origin : destination)}
      >
        Select {label}
      </button>
      {error && <span>{error}</span>}
    </div>
  )
}));

async function selectBoth() {
  await userEvent.click(screen.getByRole("button", { name: "Select Origin" }));
  await userEvent.click(screen.getByRole("button", { name: "Select Destination" }));
}

describe("TripPlannerForm", () => {
  it("shows an unavailable date control without submitting a date", async () => {
    const onSubmit = vi.fn();
    render(<TripPlannerForm loading={false} onSubmit={onSubmit} />);
    const date = screen.getByLabelText("Travel date");
    expect(date).toBeDisabled();
    expect(date).toHaveAccessibleDescription("Feature not implemented");
    await selectBoth();
    await userEvent.click(screen.getByRole("button", { name: "Find routes" }));
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty("service_date");
  });

  it("uses default 50/50 priorities", () => {
    render(<TripPlannerForm loading={false} onSubmit={() => undefined} />);
    expect(screen.getByText("Reliability").parentElement).toHaveTextContent("50%");
    expect(screen.getByText("Travel time").parentElement).toHaveTextContent("50%");
  });

  it("swaps origin and destination", async () => {
    render(<TripPlannerForm loading={false} onSubmit={() => undefined} />);
    await selectBoth();
    await userEvent.click(screen.getByRole("button", { name: "Swap origin and destination" }));
    expect(screen.getByText("Origin: UBC Exchange")).toBeVisible();
    expect(screen.getByText("Destination: Granville Station")).toBeVisible();
  });

  it("rejects the same selected stop", async () => {
    render(<TripPlannerForm loading={false} onSubmit={() => undefined} />);
    await selectBoth();
    await userEvent.click(screen.getByRole("button", { name: "Swap origin and destination" }));
    await userEvent.click(screen.getByRole("button", { name: "Select Destination" }));
    await userEvent.click(screen.getByRole("button", { name: "Find routes" }));
    expect(screen.getByText("Origin and destination must be different.")).toBeVisible();
  });

  it("priority and advanced settings affect the API request", async () => {
    const onSubmit = vi.fn();
    render(<TripPlannerForm loading={false} onSubmit={onSubmit} />);
    await selectBoth();
    fireEvent.change(screen.getByLabelText("Reliability priority percentage"), {
      target: { value: "70" }
    });
    await userEvent.click(screen.getByText("Advanced options"));
    fireEvent.change(screen.getByLabelText("Transfer priority (%)"), {
      target: { value: "20" }
    });
    fireEvent.change(screen.getByLabelText("Minimum samples"), {
      target: { value: "35" }
    });
    fireEvent.change(screen.getByLabelText("Maximum extra minutes"), {
      target: { value: "45" }
    });
    fireEvent.change(screen.getByLabelText("Search timeout (seconds)"), {
      target: { value: "40" }
    });
    await userEvent.click(screen.getByRole("button", { name: "Find routes" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        reliability_effect: 0.7,
        travel_time_effect: 0.3,
        transfer_effect: 0.2,
        minimum_samples: 35,
        max_extra_minutes: 45,
        search_timeout_seconds: 40
      })
    );
  });

  it("disables duplicate submission while loading", () => {
    render(<TripPlannerForm loading onSubmit={() => undefined} />);
    expect(screen.getByRole("button", { name: "Comparing routes…" })).toBeDisabled();
  });
});
