import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, searchStops } from "../api/client";
import { origin } from "../test/fixtures";
import { StopAutocomplete } from "./StopAutocomplete";

vi.mock("../api/client", async (original) => {
  const actual = await original<typeof import("../api/client")>();
  return { ...actual, searchStops: vi.fn() };
});

describe("StopAutocomplete", () => {
  beforeEach(() => vi.mocked(searchStops).mockReset());

  it("waits until at least two characters before searching", async () => {
    render(
      <StopAutocomplete
        id="origin"
        label="Origin"
        placeholder="Search"
        value={null}
        onChange={() => undefined}
      />
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "G" } });
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(searchStops).not.toHaveBeenCalled();
  });

  it("displays and selects returned stops", async () => {
    vi.mocked(searchStops).mockResolvedValue([origin]);
    const onChange = vi.fn();
    render(
      <StopAutocomplete
        id="origin"
        label="Origin"
        placeholder="Search"
        value={null}
        onChange={onChange}
      />
    );
    await userEvent.type(screen.getByRole("combobox"), "Gr");
    expect(await screen.findByText("Granville Station", {}, { timeout: 1000 })).toBeVisible();
    await userEvent.click(screen.getByText("Granville Station"));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(origin));
  });

  it("distinguishes no matches, request failure, and unavailable API", async () => {
    vi.mocked(searchStops).mockResolvedValueOnce([]);
    const { rerender } = render(
      <StopAutocomplete
        id="origin"
        label="Origin"
        placeholder="Search"
        value={null}
        onChange={() => undefined}
      />
    );
    await userEvent.type(screen.getByRole("combobox"), "No");
    expect(await screen.findByText("No matching stops found", {}, { timeout: 1000 })).toBeVisible();

    vi.mocked(searchStops).mockRejectedValueOnce(new ApiError("network", undefined, "network"));
    await userEvent.clear(screen.getByRole("combobox"));
    await userEvent.type(screen.getByRole("combobox"), "Er");
    expect(await screen.findByText(/Stop search failed/, {}, { timeout: 1000 })).toBeVisible();
    expect(screen.queryByText("No matching stops found")).not.toBeInTheDocument();

    rerender(
      <StopAutocomplete
        id="origin"
        label="Origin"
        placeholder="Search"
        value={null}
        onChange={() => undefined}
        apiAvailable={false}
      />
    );
    await userEvent.clear(screen.getByRole("combobox"));
    await userEvent.type(screen.getByRole("combobox"), "Of");
    expect(
      await screen.findByText("Planner is currently unavailable.", {}, { timeout: 1000 })
    ).toBeVisible();
  });
});
