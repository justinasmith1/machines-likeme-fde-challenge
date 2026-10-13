// What an intake user does and sees: upload, the list, and its automatic refresh.

import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { InvoicesView } from "@/components/InvoicesView";
import { ApiError } from "@/lib/api/errors";
import { createFakeApi } from "./fake-api";
import { chooseFile, session, show } from "./screen-helpers";

describe("intake", () => {
  test("upload, then the list updates by itself until the document is processed", async () => {
    vi.useFakeTimers();
    try {
      const { api, calls } = createFakeApi({ role: "INTAKE" });
      show(api, <InvoicesView session={session("INTAKE", "ines")} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("No documents have been uploaded yet.")).toBeTruthy();
      expect(screen.getByText(/Accepted: PDF, PNG, JPG\. Maximum size: 10 MB/)).toBeTruthy();

      chooseFile("invoice.pdf");
      fireEvent.click(screen.getByRole("button", { name: "Upload" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(calls.uploadInvoice).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/was received and is being processed/)).toBeTruthy();
      expect(screen.getByText(/Processing/)).toBeTruthy();

      const before = calls.listInvoices.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect(calls.listInvoices.mock.calls.length).toBe(before + 1);
      expect(screen.getByText(/Held for review/)).toBeTruthy();
      expect(screen.getByText(/unit price 30.00 differs from PO price 25.00/)).toBeTruthy();

      // Nothing is pending any more: the page stops asking.
      const settled = calls.listInvoices.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000);
      });
      expect(calls.listInvoices.mock.calls.length).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  test("sees only file, date, status and plain reasons: no codes, no reviewer, no links to the detail", async () => {
    const { api, state } = createFakeApi({ role: "INTAKE" });
    state.intake = [
      { id: 1, filename: "invoice.pdf", uploaded_at: "2026-10-12T09:00:00+00:00", status: "HELD_FOR_REVIEW", reasons: ["Line 1: quantity is illegible; the system does not infer it"] },
      { id: 2, filename: "quote.pdf", uploaded_at: "2026-10-12T09:05:00+00:00", status: "NOT_AN_INVOICE", reasons: ["Received, not an invoice: quotation (Titled Quotation.)"] },
      { id: 3, filename: "paid.pdf", uploaded_at: "2026-10-12T09:06:00+00:00", status: "AUTO_PAY", reasons: [] },
    ];
    const { container } = show(api, <InvoicesView session={session("INTAKE", "ines")} />);
    await screen.findByText("quote.pdf");

    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "File", "Uploaded", "Status", "What it means",
    ]);
    expect(screen.getByText("Received, not an invoice: quotation (Titled Quotation.)")).toBeTruthy();
    // A status with no reasons still gets a sentence, so the row is never just a colour.
    expect(screen.getByText(/matched its purchase order and receipts/)).toBeTruthy();
    expect(screen.queryByLabelText("Status")).toBeNull(); // no reviewer filter
    expect(container.querySelector('a[href^="/invoices/"]')).toBeNull();
    for (const internal of ["A6", "DL-", "ASSUMPTION", "SUP-002", "ana.analyst", "Parameters"]) {
      expect(container.textContent).not.toContain(internal);
    }
  });

  test("a file of the wrong type or over the limit is refused before it is sent", async () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    show(api, <InvoicesView session={session("INTAKE", "ines")} />);
    await screen.findByText(/Maximum size: 10 MB/);

    chooseFile("invoice.docx", "x", "application/msword");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("alert").textContent).toContain("is not a supported file type. Upload PDF, PNG, JPG.");

    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Document"), { target: { files: [big] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("alert").textContent).toContain("is larger than the 10 MB limit");
    expect(calls.uploadInvoice).not.toHaveBeenCalled();
  });

  test("when the API refuses an upload, its message is shown", async () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    calls.uploadInvoice.mockRejectedValueOnce(new ApiError(413, "FILE_TOO_LARGE", "The file is larger than the 10 MB limit."));
    show(api, <InvoicesView session={session("INTAKE", "ines")} />);
    await screen.findByText(/Maximum size/);
    chooseFile("invoice.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    const alert = await screen.findByRole("alert");
    expect(alert.getAttribute("data-error-code")).toBe("FILE_TOO_LARGE");
    expect(alert.textContent).toContain("File too large. The file is larger than the 10 MB limit.");
  });

  test("loading, then an error with a way to retry", async () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    calls.listInvoices.mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "down"));
    show(api, <InvoicesView session={session("INTAKE", "ines")} />);
    expect(screen.getByText("Loading documents…")).toBeTruthy();
    const alert = await screen.findByText(/The service cannot be reached/);
    expect(alert).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("No documents have been uploaded yet.");
  });
});
