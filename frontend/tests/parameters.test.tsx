// Parameters: read only for the analyst, editable with a comment for the manager.

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ParametersView } from "@/components/ParametersView";
import { createFakeApi } from "./fake-api";
import { session, show } from "./screen-helpers";

describe("parameters for the analyst", () => {
  test("read only: values, no form, no history request", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <ParametersView session={session("AP_ANALYST")} />);
    await screen.findByText(/2% of the purchase order unit price/);
    expect(screen.getByText(/5,000\.00 \(above it, the AP Manager decides\)/)).toBeTruthy();
    expect(screen.getByText("Only the AP Manager can change these values.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save new version" })).toBeNull();
    expect(calls.parameterHistory).not.toHaveBeenCalled();
  });
});

describe("parameters for the manager", () => {
  test("edits parameters with a mandatory comment and sees the history", async () => {
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <ParametersView session={session("AP_MANAGER", "max.manager")} />);
    const tolerance = (await screen.findByLabelText(/Price tolerance/)) as HTMLInputElement;
    expect(tolerance.value).toBe("0.02");
    await screen.findByText("Initial values from configuration.");

    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    expect(screen.getByText("Nothing was changed.")).toBeTruthy();

    fireEvent.change(tolerance, { target: { value: "0.05" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    expect(screen.getByText("A comment explaining the change is required.")).toBeTruthy();
    expect(calls.updateParameters).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Comment (required)"), { target: { value: "Agreed with the CFO." } });
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await screen.findByText("Version 2 saved. It applies to new evaluations.");
    expect(calls.updateParameters).toHaveBeenCalledWith({ price_tolerance_pct: "0.05" }, "Agreed with the CFO.");
    await screen.findByText("Agreed with the CFO.");
    expect(screen.getByText(/5% of the purchase order unit price/)).toBeTruthy();
  });
});

describe("A6, A7 and A9 are fixed rules (DL-18)", () => {
  test("they are listed as always on, and are not among the switches", async () => {
    const { api } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <ParametersView session={session("AP_MANAGER", "max.manager")} />);
    await screen.findByText("Parameters in force");

    const fixed = screen.getByText("Always on").nextElementSibling!;
    expect(fixed.textContent).toMatch(/quantity or unit price is illegible\s*A6/);
    expect(fixed.textContent).toMatch(/purchase order number is illegible\s*A7/);
    expect(fixed.textContent).toMatch(/invoice number or date is missing or illegible\s*A9/);
    expect(fixed.textContent).toMatch(/They\s+cannot be switched off\./);
    expect(fixed.textContent).toMatch(/illegible field was confirmed by the client \(DL-18\)/);
    expect(fixed.textContent).toMatch(/missing invoice number or date is an assumption \(DL-35\)/);

    const switches = screen.getAllByRole("checkbox").map((box) => box.closest("label")!.textContent!);
    expect(switches).toHaveLength(7);
    for (const code of ["A6", "A7", "A9"]) {
      expect(switches.some((label) => label.trim().endsWith(code))).toBe(false);
    }
    expect(switches.map((label) => label.trim().split(" ").pop())).toEqual(["A1", "A2", "A3", "A4", "A5", "A8", "A10"]);
    expect(screen.getByText("Additional checks (rules from the specification, A6, A7 and A9 are always on)")).toBeTruthy();
  });

  test("saving the switches never sends a fixed rule", async () => {
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <ParametersView session={session("AP_MANAGER", "max.manager")} />);
    await screen.findByText("Parameters in force");
    fireEvent.click(screen.getAllByRole("checkbox")[0]); // switch A1 off
    fireEvent.change(screen.getByLabelText("Comment (required)"), { target: { value: "A1 off." } });
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await screen.findByText(/Version 2 saved/);
    const sent = calls.updateParameters.mock.calls[0][0] as { enabled_assumption_rules: string[] };
    expect(sent.enabled_assumption_rules).toEqual(["A2", "A3", "A4", "A5", "A8", "A10"]);
  });

  test("the summary lists only the switchable checks as on or off", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <ParametersView session={session("AP_ANALYST")} />);
    await screen.findByText("Parameters in force");
    const enabled = screen.getByText("Additional checks enabled").nextElementSibling!;
    expect(enabled.querySelectorAll("li")).toHaveLength(7);
    expect(enabled.textContent).not.toMatch(/A6|A7|A9/);
  });
});
