// The review queue and the invoice detail for reviewers: reading status, suggestions,
// decisions, corrections, retry, and the audit trail for the manager.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ReviewQueueView } from "@/components/ReviewQueueView";
import { InvoiceDetailView } from "@/components/invoice/InvoiceDetailView";
import { ApiError } from "@/lib/api/errors";
import { CORRECTED_REASON, LIMIT_NOTICE, NO_PO_WARNING, createFakeApi } from "./fake-api";
import { session, show } from "./screen-helpers";

describe("analyst: review queue and invoice detail", () => {
  test("the queue lists held invoices with a link to each", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <ReviewQueueView />);
    const link = await screen.findByRole("link", { name: "invoice.pdf" });
    expect(link.getAttribute("href")).toBe("/invoices/1?from=review");
    expect(screen.getByText(/Held for review/)).toBeTruthy();
  });

  test("an empty queue says so", async () => {
    const { api, state } = createFakeApi({ role: "AP_ANALYST" });
    state.status = "AUTO_PAY";
    show(api, <ReviewQueueView />);
    await screen.findByText("Nothing is waiting for review.");
  });

  test("the detail shows the document next to the data, the reading status, the mapping and the reasons", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);

    await screen.findByText("Extracted data");
    await waitFor(() => expect(screen.getByTitle("Original document invoice.pdf")).toBeTruthy());
    expect(calls.getDocument).toHaveBeenCalledWith(1);

    expect(screen.getByText("TEST-0001")).toBeTruthy();
    expect(screen.getAllByText(/Read$/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Illegible/)).toBeTruthy();
    expect(screen.getByText("PO-2026-0002, line 1")).toBeTruthy();
    expect(screen.getByText("Same description as the PO line")).toBeTruthy();

    const reasons = screen.getByRole("table", { name: "Every rule that fired for this invoice" });
    expect(within(reasons).getByText("A6")).toBeTruthy();
    expect(within(reasons).getByText("Line 1: quantity is illegible; the system does not infer it")).toBeTruthy();
    expect(within(reasons).getByText("Confirmed by client (DL-18)")).toBeTruthy(); // A6 is a fixed rule
    // With no decision yet there is no empty "Decisions" part in the history.
    expect(screen.queryByText("Decisions")).toBeNull();
    expect(screen.getByText(/Version 1/, { selector: "strong" })).toBeTruthy();
  });

  test("a suggested value is shown as a suggestion, never as the value read (DL-18)", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");

    const suggestion = screen.getByRole("note");
    expect(suggestion.className).toBe("suggestion");
    expect(within(suggestion).getByText("Suggestion")).toBeTruthy();
    expect(suggestion.textContent).toContain("4");
    expect(suggestion.textContent).toContain("not a value read from the document");
    // The value cell of that field stays empty and says so.
    const cell = suggestion.closest(".readfield")!;
    expect(cell.querySelector(".value")!.textContent).toBe("No value read");
    expect(cell.querySelector(".value")!.className).toContain("value-missing");
  });

  test("only the decisions the API lists are offered", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    const group = await screen.findByRole("group", { name: "What should happen to this invoice?" });
    expect(within(group).getAllByRole("radio").map((r) => (r as HTMLInputElement).value)).toEqual([
      "APPROVE", "REJECT", "REQUEST_CREDIT_NOTE",
    ]);
    expect(within(group).queryByLabelText("Override and approve")).toBeNull();
  });

  test("a decision needs a comment, then it is recorded and the status changes", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    fireEvent.click(await screen.findByLabelText("Approve for payment"));
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    expect(screen.getByText("A comment is required to record a decision.")).toBeTruthy();
    expect(calls.decide).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Checked against the delivery note." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    await screen.findByText("Decision recorded: Approve for payment.");
    expect(calls.decide).toHaveBeenCalledWith(1, "APPROVE", "Checked against the delivery note.");
    expect(screen.getAllByText(/Approved for payment/).length).toBeGreaterThan(0);
    expect(screen.getByText("Checked against the delivery note.")).toBeTruthy();
    expect(screen.getByText("In force")).toBeTruthy();
    // Already payable: approving again is no longer offered.
    expect(screen.queryByLabelText("Approve for payment")).toBeNull();
  });

  test("above the limit, approval is disabled for the analyst with the API's notice; rejecting stays available", async () => {
    const { api } = createFakeApi({ role: "AP_ANALYST", total: "6000.00" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    const approve = (await screen.findByLabelText("Approve for payment")) as HTMLInputElement;
    expect(approve.disabled).toBe(true);
    expect(screen.getByText(`Not available to you. ${LIMIT_NOTICE}`)).toBeTruthy();
    expect((screen.getByLabelText("Reject") as HTMLInputElement).disabled).toBe(false);
  });

  test("a 403 from the API is shown clearly even when the button was offered, and the form stays usable", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    calls.decide.mockRejectedValueOnce(new ApiError(403, "PERMISSION_DENIED", LIMIT_NOTICE));
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    fireEvent.click(await screen.findByLabelText("Approve for payment"));
    const comment = screen.getByLabelText("Comment (required)", { selector: "#decision-comment" });
    fireEvent.change(comment, { target: { value: "Approved." } });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    const alert = await screen.findByRole("alert");
    expect(alert.getAttribute("data-error-code")).toBe("PERMISSION_DENIED");
    expect(alert.textContent).toBe(`Not allowed. ${LIMIT_NOTICE}`);
    expect(screen.getAllByText(/Held for review/).length).toBeGreaterThan(0); // nothing changed
    expect((comment as HTMLTextAreaElement).value).toBe("Approved."); // the comment is not lost

    fireEvent.click(screen.getByLabelText("Reject"));
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Reject.");
  });

  test("a 409 from the API explains that the action no longer applies", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    calls.decide.mockRejectedValueOnce(
      new ApiError(409, "INVALID_STATE", "Invoice 1 is already AUTO_PAY; the decision would change nothing."),
    );
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    fireEvent.click(await screen.findByLabelText("Reject"));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), { target: { value: "No." } });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "This action no longer applies. Invoice 1 is already AUTO_PAY; the decision would change nothing.",
    );
  });

  test("correcting keeps the invoice held and says approval is separate; then the same analyst approves (DL-46)", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    expect((await screen.findByText(/Saving a correction does not approve payment/)).textContent).toContain(
      "stays held until someone records a decision",
    );

    fireEvent.click(screen.getByRole("button", { name: "Correct data" }));
    // The suggestion was not filled in for the reviewer.
    const quantity = screen.getByLabelText("Quantity") as HTMLInputElement;
    expect(quantity.value).toBe("");
    fireEvent.change(quantity, { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(screen.getByText("A comment explaining the correction is required.")).toBeTruthy();
    expect(calls.correctExtraction).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#correction-comment" }), {
      target: { value: "Read from the paper copy." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));

    await screen.findByText(/Correction saved\./);
    expect(calls.correctExtraction).toHaveBeenCalledWith(
      1,
      { lines: [{ line_number: 1, quantity: "4" }] },
      "Read from the paper copy.",
    );
    // Still held, with the reason, although the engine now says Auto-Pay.
    expect(screen.getAllByText(/Held for review/).length).toBeGreaterThan(0);
    expect(screen.getByText(new RegExp(CORRECTED_REASON.slice(0, 40)))).toBeTruthy();
    expect(screen.getByText(/approving is a separate action/)).toBeTruthy();
    expect(screen.getByText(/it stays held until someone records a decision below/)).toBeTruthy();
    expect(screen.getByText("No rule fired. Every check passed.")).toBeTruthy();
    expect(calls.decide).not.toHaveBeenCalled();

    // Approval is its own action, with its own comment.
    fireEvent.click(screen.getByLabelText("Approve for payment"));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Checked against the delivery note." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");
    expect(calls.decide).toHaveBeenCalledWith(1, "APPROVE", "Checked against the delivery note.");
    expect(screen.queryByText(/Correction saved/)).toBeNull();
  });

  test("a correction with nothing changed is not sent", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    fireEvent.click(await screen.findByRole("button", { name: "Correct data" }));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#correction-comment" }), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(screen.getByText("Nothing was changed.")).toBeTruthy();
    expect(calls.correctExtraction).not.toHaveBeenCalled();
  });

  test("a failed extraction offers a retry, and no decision", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    calls.getInvoice.mockImplementation(async () => {
      const detail = await createFakeApi({ role: "AP_ANALYST" }).api.getInvoice(1);
      if (!("invoice" in detail)) throw new Error("unexpected");
      return {
        ...detail,
        invoice: { ...detail.invoice, status: "EXTRACTION_FAILED", status_reason: "The model service returned HTTP 503." },
        actions: { decisions: [], can_correct: false, can_retry: true },
        extraction: null,
        evaluation: null,
        extraction_versions: [],
      };
    });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry extraction" }));
    await waitFor(() => expect(calls.retryExtraction).toHaveBeenCalledWith(1));
    expect(screen.getByText("The model service returned HTTP 503.")).toBeTruthy();
    expect(screen.getByText("No decision applies to this document in its current status.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Correct data" })).toBeNull();
  });

  test("the analyst does not get the audit trail, and it is not even requested", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");
    expect(screen.queryByText("Audit trail")).toBeNull();
    expect(calls.getAudit).not.toHaveBeenCalled();
  });

  test("an invoice that does not exist shows the not-found message", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    calls.getInvoice.mockRejectedValueOnce(new ApiError(404, "NOT_FOUND", "Invoice 99 does not exist."));
    show(api, <InvoiceDetailView invoiceId={99} session={session("AP_ANALYST")} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Not found. Invoice 99 does not exist.");
  });
});

describe("manager", () => {
  test("approves above the limit, with the notice visible", async () => {
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max.manager", total: "6000.00" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    const approve = (await screen.findByLabelText("Approve for payment")) as HTMLInputElement;
    expect(approve.disabled).toBe(false);
    expect(screen.getByText(LIMIT_NOTICE)).toBeTruthy();
    fireEvent.click(approve);
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Confirmed with the supplier." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");
    expect(calls.decide).toHaveBeenCalledWith(1, "APPROVE", "Confirmed with the supplier.");
  });

  test("sees the audit trail as a timeline", async () => {
    const { api } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    await screen.findByText("Audit trail");
    expect(await screen.findByText("Document uploaded")).toBeTruthy();
    expect(screen.getByText("Rules evaluated")).toBeTruthy();
    expect(screen.getByText(/ines\.intake \(Intake\)/)).toBeTruthy();
  });

  test("if the API refuses the audit trail, that is shown and the rest of the page still works", async () => {
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    calls.getAudit.mockRejectedValue(new ApiError(403, "PERMISSION_DENIED", "This action requires the role AP_MANAGER."));
    show(api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Not allowed. This action requires the role AP_MANAGER.",
    );
    expect(screen.getByText("Extracted data")).toBeTruthy();
    expect(screen.getByLabelText("Reject")).toBeTruthy();
  });
});
