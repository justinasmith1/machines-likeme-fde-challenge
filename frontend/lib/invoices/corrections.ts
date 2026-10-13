// Builds the correction a reviewer sends from an edited copy of the extracted data.
// Only the fields that changed are sent; everything else stays as it was (DL-12).
//
// An empty field means "not on the document" and is sent as null. A value typed by a
// person counts as read, never as a suggestion (DL-50).

import type { Corrections, Extraction } from "@/lib/api/types";

export const DOCUMENT_TYPES = ["invoice", "quotation", "credit_note", "other"] as const;

export const HEADER_FIELDS = [
  { name: "supplier_name", label: "Supplier name" },
  { name: "supplier_tax_id", label: "Supplier tax ID" },
  { name: "invoice_number", label: "Invoice number" },
  { name: "invoice_date", label: "Invoice date (YYYY-MM-DD)" },
  { name: "due_date", label: "Due date (YYYY-MM-DD)" },
  { name: "po_references", label: "Purchase order numbers (comma separated)" },
  { name: "currency", label: "Currency" },
  { name: "subtotal", label: "Subtotal" },
  { name: "tax_amount", label: "Tax" },
  { name: "total_amount", label: "Total" },
] as const;

export const LINE_FIELDS = [
  { name: "description", label: "Description" },
  { name: "quantity", label: "Quantity" },
  { name: "unit_price", label: "Unit price" },
  { name: "line_total", label: "Line total" },
  { name: "po_line_reference", label: "PO line" },
] as const;

export type HeaderFieldName = (typeof HEADER_FIELDS)[number]["name"];
export type LineFieldName = (typeof LINE_FIELDS)[number]["name"];

export interface CorrectionForm {
  document_type: string;
  header: Record<HeaderFieldName, string>;
  lines: Array<Record<LineFieldName, string>>;
}

const text = (value: string | number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value);

/** The current extraction as editable text. Suggestions are not copied into the fields. */
export function toCorrectionForm(extraction: Extraction): CorrectionForm {
  const header = extraction.header;
  return {
    document_type: extraction.document_type ?? "invoice",
    header: {
      supplier_name: text(header?.supplier_name),
      supplier_tax_id: text(header?.supplier_tax_id),
      invoice_number: text(header?.invoice_number.value),
      invoice_date: text(header?.invoice_date.value),
      due_date: text(header?.due_date),
      po_references: (header?.po_references.values ?? []).join(", "),
      currency: text(header?.currency),
      subtotal: text(header?.subtotal),
      tax_amount: text(header?.tax_amount),
      total_amount: text(header?.total_amount),
    },
    lines: (extraction.lines ?? []).map((line) => ({
      description: text(line.description),
      quantity: text(line.quantity.value),
      unit_price: text(line.unit_price.value),
      line_total: text(line.line_total),
      po_line_reference: text(line.po_line_printed_on_document),
    })),
  };
}

function splitReferences(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** The differences between the original and the edited form, or null if nothing changed. */
export function buildCorrections(original: CorrectionForm, edited: CorrectionForm): Corrections | null {
  const corrections: Corrections = {};

  if (edited.document_type !== original.document_type) {
    corrections.document_type = edited.document_type;
  }

  const header: NonNullable<Corrections["header"]> = {};
  for (const { name } of HEADER_FIELDS) {
    const before = original.header[name].trim();
    const after = edited.header[name].trim();
    if (before === after) continue;
    if (name === "po_references") {
      const beforeList = splitReferences(before);
      const afterList = splitReferences(after);
      if (beforeList.join("|") !== afterList.join("|")) header[name] = afterList;
    } else {
      header[name] = after === "" ? null : after;
    }
  }
  if (Object.keys(header).length > 0) corrections.header = header;

  const lines: NonNullable<Corrections["lines"]> = [];
  edited.lines.forEach((line, index) => {
    const source = original.lines[index];
    if (!source) return;
    const change: { line_number: number } & Record<string, string | number | null> = {
      line_number: index + 1,
    };
    for (const { name } of LINE_FIELDS) {
      const before = source[name].trim();
      const after = line[name].trim();
      if (before === after) continue;
      if (name === "po_line_reference") {
        change[name] = after === "" ? null : Number(after);
      } else {
        change[name] = after === "" ? null : after;
      }
    }
    if (Object.keys(change).length > 1) lines.push(change);
  });
  if (lines.length > 0) corrections.lines = lines;

  return Object.keys(corrections).length > 0 ? corrections : null;
}

/** Problems a person can fix before sending, so the API is not used as a spell checker. */
export function validateCorrectionForm(form: CorrectionForm): string[] {
  const problems: string[] = [];
  form.lines.forEach((line, index) => {
    const reference = line.po_line_reference.trim();
    if (reference !== "" && !/^[1-9]\d*$/.test(reference)) {
      problems.push(`Line ${index + 1}: the PO line must be a whole number, or empty.`);
    }
  });
  return problems;
}
