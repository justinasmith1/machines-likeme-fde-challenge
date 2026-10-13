// The correction a reviewer sends: only what changed, and never a suggestion.

import { describe, expect, test } from "vitest";
import type { Extraction } from "@/lib/api/types";
import { buildCorrections, toCorrectionForm, validateCorrectionForm } from "@/lib/invoices/corrections";

describe("corrections", () => {
  const extraction: Extraction = {
    version: 1,
    source: "MODEL",
    created_by: "system",
    created_at: "2026-10-12T10:00:00+00:00",
    comment: null,
    model: "m",
    prompt_version: "2",
    document_type: "invoice",
    classification_reason: "Requests payment.",
    header: {
      supplier_name: "Test Supplier B",
      supplier_tax_id: "TAX-B",
      invoice_number: { value: "TEST-0001", status: "read", suggestion: null },
      invoice_date: { value: null, status: "illegible", suggestion: { value: "2026-02-15", is_suggestion: true, note: "n" } },
      due_date: null,
      po_references: { values: ["PO-2026-0002"], status: "read", suggestion: null },
      currency: "EUR",
      subtotal: "100.00",
      tax_amount: "21.00",
      total_amount: "121.00",
    },
    lines: [
      {
        line_number: 1,
        description: "Synthetic test item",
        quantity: { value: null, status: "illegible", suggestion: { value: "4", is_suggestion: true, note: "n" } },
        unit_price: { value: "25.00", status: "read", suggestion: null },
        line_total: "100.00",
        po_line_printed_on_document: null,
        po_line_mapping: null,
      },
    ],
  };

  test("suggestions are never copied into the form", () => {
    const form = toCorrectionForm(extraction);
    expect(form.header.invoice_date).toBe("");
    expect(form.lines[0].quantity).toBe("");
    expect(form.header.po_references).toBe("PO-2026-0002");
  });

  test("an untouched form produces no correction", () => {
    const form = toCorrectionForm(extraction);
    expect(buildCorrections(form, structuredClone(form))).toBeNull();
  });

  test("only changed fields are sent", () => {
    const original = toCorrectionForm(extraction);
    const edited = structuredClone(original);
    edited.header.invoice_date = " 2026-02-15 ";
    edited.lines[0].quantity = "4";
    expect(buildCorrections(original, edited)).toEqual({
      header: { invoice_date: "2026-02-15" },
      lines: [{ line_number: 1, quantity: "4" }],
    });
  });

  test("an emptied field is sent as null, meaning it is not on the document", () => {
    const original = toCorrectionForm(extraction);
    const edited = structuredClone(original);
    edited.header.supplier_tax_id = "";
    edited.lines[0].unit_price = "  ";
    expect(buildCorrections(original, edited)).toEqual({
      header: { supplier_tax_id: null },
      lines: [{ line_number: 1, unit_price: null }],
    });
  });

  test("purchase orders are sent as a list, and the PO line as a number", () => {
    const original = toCorrectionForm(extraction);
    const edited = structuredClone(original);
    edited.header.po_references = "PO-2026-0002,  PO-2026-0003 ";
    edited.lines[0].po_line_reference = "2";
    edited.document_type = "quotation";
    expect(buildCorrections(original, edited)).toEqual({
      document_type: "quotation",
      header: { po_references: ["PO-2026-0002", "PO-2026-0003"] },
      lines: [{ line_number: 1, po_line_reference: 2 }],
    });
    edited.header.po_references = "";
    expect(buildCorrections(original, edited)!.header).toEqual({ po_references: [] });
  });

  test("spacing alone in the purchase order list is not a change", () => {
    const original = toCorrectionForm(extraction);
    const edited = structuredClone(original);
    edited.header.po_references = " PO-2026-0002 ,";
    expect(buildCorrections(original, edited)).toBeNull();
  });

  test("a PO line that is not a whole number is caught before sending", () => {
    const form = toCorrectionForm(extraction);
    form.lines[0].po_line_reference = "one";
    expect(validateCorrectionForm(form)).toEqual(["Line 1: the PO line must be a whole number, or empty."]);
    form.lines[0].po_line_reference = "2";
    expect(validateCorrectionForm(form)).toEqual([]);
  });
});
