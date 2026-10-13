// Invoice detail: who set each value, earlier evaluations, advice when no purchase order
// is matched, the supplier's name, a readable audit trail, and the version list.

import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ReviewQueueView } from "@/components/ReviewQueueView";
import { InvoiceDetailView } from "@/components/invoice/InvoiceDetailView";
import { NO_PO_WARNING, createFakeApi } from "./fake-api";
import { session, show } from "./screen-helpers";

async function correctQuantity(value = "4") {
  fireEvent.click(await screen.findByRole("button", { name: "Correct data" }));
  fireEvent.change(screen.getByLabelText("Quantity"), { target: { value } });
  fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#correction-comment" }), {
    target: { value: "Read from the paper copy." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await screen.findByText(/Correction saved\./);
}

function quantityCell(): HTMLElement {
  const table = screen.getByRole("table", {
    name: "Invoice lines and the purchase order line each one was matched to",
  });
  return within(table).getAllByRole("cell")[2]; // #, description, quantity
}

describe("origin of each value", () => {
  test("what the model read is tagged Read or Illegible", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");
    expect(within(quantityCell()).getByText(/Illegible/)).toBeTruthy();
    expect(quantityCell().querySelector(".tag-corrected")).toBeNull();
  });

  test("a value typed by a person says who corrected it and what it was, and is not tagged Read", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await correctQuantity("4");

    const cell = quantityCell();
    expect(cell.querySelector(".value")!.textContent).toBe("4");
    const tag = cell.querySelector(".tag-corrected")!;
    expect(tag.textContent).toContain("Corrected by ana.analyst");
    expect(tag.textContent).toContain("(was illegible)");
    expect(cell.querySelector(".tag-read")).toBeNull();
    expect(cell.textContent).not.toMatch(/✓\s*Read/);
    // The suggestion is gone with the correction, and fields nobody touched are still the model's.
    expect(cell.querySelector(".suggestion")).toBeNull();
    const unitPrice = within(screen.getByRole("table", { name: /Invoice lines/ })).getAllByRole("cell")[3];
    expect(unitPrice.querySelector(".tag-read")).not.toBeNull();
    expect(unitPrice.querySelector(".tag-corrected")).toBeNull();
  });

  test("the corrected tag has its own style, apart from what the model read", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await correctQuantity();
    const tag = quantityCell().querySelector(".tag-corrected")!;
    expect(tag.className).toBe("tag tag-corrected");
    expect(tag.textContent).toContain("✎"); // a symbol, not only a colour
  });
});

describe("earlier evaluations", () => {
  test("with a single evaluation there is nothing earlier to show, so the part is left out", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");
    expect(screen.queryByText("Earlier evaluations")).toBeNull();
  });

  test("after a correction, the original evaluation stays visible with its result and rules", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await correctQuantity();

    // The evaluation in force has no reasons any more...
    const current = screen.getByRole("heading", { name: "Rules evaluation" }).closest("section")!;
    expect(within(current).getByText("No rule fired. Every check passed.")).toBeTruthy();

    // ...and the earlier one still explains why it was held.
    const earlier = screen.getByText("Earlier evaluations").closest("details")!;
    expect(within(earlier).getByText("Evaluation of extraction version 1")).toBeTruthy();
    // Its result, and the effect of the rule that fired.
    expect(within(earlier).getAllByText(/Held for review/).length).toBeGreaterThanOrEqual(1);
    expect(earlier.querySelector(".timeline > li .badge")!.textContent).toContain("Held for review");
    const rules = within(earlier).getByRole("table", {
      name: "Rules that fired in the evaluation of extraction version 1",
    });
    expect(within(rules).getByText("A6")).toBeTruthy();
    expect(within(rules).getByText("Line 1: quantity is illegible; the system does not infer it")).toBeTruthy();
    expect(within(rules).getByText("Confirmed by client (DL-18)")).toBeTruthy();
  });
});

describe("approving with no purchase order matched", () => {
  test("the API's advice is shown next to the approval, which stays available", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST", noPurchaseOrder: true });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    const approve = (await screen.findByLabelText("Approve for payment")) as HTMLInputElement;
    expect(approve.disabled).toBe(false);

    const advice = approve.closest(".choice")!.querySelector(".hint-warning")!;
    expect(advice.textContent).toContain(NO_PO_WARNING);
    expect(advice.textContent).toContain("Consider correcting the purchase order number first.");
    expect(advice.textContent).toContain("⚠");
    // Only decisions that release payment carry it.
    expect((screen.getByLabelText("Reject") as HTMLInputElement).closest(".choice")!.querySelector(".hint-warning")).toBeNull();

    fireEvent.click(approve);
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "PO confirmed by phone." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");
    expect(calls.decide).toHaveBeenCalledWith(1, "APPROVE", "PO confirmed by phone.");
  });

  test("no advice when a line is matched to a purchase order", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    const { container } = show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByLabelText("Approve for payment");
    expect(container.querySelector(".hint-warning")).toBeNull();
  });
});

describe("supplier", () => {
  test("the detail header shows the legal name with the code", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");
    const summary = screen.getByRole("region", { name: "Invoice summary" });
    expect(within(summary).getByText("Test Supplier B (SUP-002)")).toBeTruthy();
  });

  test("the review queue shows it too", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <ReviewQueueView />);
    expect(await screen.findByText("Test Supplier B (SUP-002)")).toBeTruthy();
  });

  test("with no name from the master, the code alone is shown", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const queue = await api.reviewQueue();
    calls.reviewQueue.mockResolvedValue([{ ...queue[0], supplier_name: null }]);
    show(api, <ReviewQueueView />);
    expect(await screen.findByText("SUP-002")).toBeTruthy();
  });
});

describe("extraction versions", () => {
  test("say 'the model', not the name of the deployment", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    const section = (await screen.findByText("Extraction versions")).closest("details")!;
    expect(section.textContent).toContain("read by the model");
    expect(section.textContent).not.toContain("test-deployment");
    expect(section.textContent).not.toContain("prompt");
  });
});

describe("audit trail in plain language", () => {
  test("events read as sentences; ids and the deployment name stay on a secondary line", async () => {
    const { api } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    const section = (await screen.findByRole("heading", { name: "Audit trail" })).closest("section")!;

    expect(await within(section).findByText("Outcome: Held for review")).toBeTruthy();
    expect(within(section).getByText("A6: Line 1: quantity is illegible; the system does not infer it")).toBeTruthy();
    expect(within(section).getByText("Read by the model.")).toBeTruthy();

    const technical = Array.from(section.querySelectorAll(".technical")).map((p) => p.textContent);
    expect(technical.some((line) => line!.includes("model test-deployment"))).toBe(true);
    expect(technical.some((line) => line!.includes("evaluation 1"))).toBe(true);
    // Nothing technical in the readable lines.
    const readable = Array.from(section.querySelectorAll(".audit-lines li")).map((li) => li.textContent).join(" | ");
    expect(readable).not.toContain("test-deployment");
    expect(readable).not.toMatch(/[{}"]/);
  });

  test("a correction shows what changed, from what to what", async () => {
    const { api } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    await correctQuantity("24");
    const section = screen.getByRole("heading", { name: "Audit trail" }).closest("section")!;
    expect(await within(section).findByText("Line 1 quantity: illegible → 24")).toBeTruthy();
    expect(within(section).getByText("Comment: Read from the paper copy.")).toBeTruthy();
  });
});
