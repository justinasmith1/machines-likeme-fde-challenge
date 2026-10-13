// Where a value comes from, and the audit trail in plain language.

import { describe, expect, test } from "vitest";
import type { AuditEvent, FieldOrigin } from "@/lib/api/types";
import { formatFileSize, supplierLabel } from "@/lib/format";
import { RULE_DESCRIPTIONS, RULE_SOURCE_LABELS, decisionChoices } from "@/lib/invoices/actions";
import { describeAuditEvent, describeChange, describeState } from "@/lib/invoices/audit";
import { describeOrigin, isCorrected } from "@/lib/invoices/origin";

const corrected = (previous_value: FieldOrigin extends infer _ ? unknown : never, previous_status: string | null): FieldOrigin =>
  ({
    source: "HUMAN_CORRECTION",
    corrected_by: "ana.analyst",
    corrected_at: "2026-10-12T09:30:00+00:00",
    version: 2,
    previous_value,
    previous_status,
  }) as FieldOrigin;

function event(event_type: string, details: Record<string, unknown>): AuditEvent {
  return { id: 1, invoice_id: 1, event_type, actor: "ana.analyst", role: "AP_ANALYST", timestamp: "2026-10-12T09:00:00+00:00", details };
}

describe("origin of a field", () => {
  test("a value read by the model shows its reading status", () => {
    expect(describeOrigin({ source: "MODEL" }, "read")).toEqual({ corrected: false, label: "Read", was: null });
    expect(describeOrigin({ source: "MODEL" }, "illegible").label).toBe("Illegible");
    expect(describeOrigin(undefined, "absent").label).toBe("Not on the document");
    expect(isCorrected({ source: "MODEL" })).toBe(false);
  });

  test("a value typed by a person is never 'Read': it says who corrected it and what it was", () => {
    const text = describeOrigin(corrected(null, "illegible"), "read");
    expect(text).toEqual({ corrected: true, label: "Corrected by ana.analyst", was: "was illegible" });
    expect(text.label).not.toContain("Read");
    expect(isCorrected(corrected(null, "illegible"))).toBe(true);
  });

  test("what the field was before, in each case", () => {
    expect(describeOrigin(corrected(null, "absent")).was).toBe("was not on the document");
    expect(describeOrigin(corrected("3", "read")).was).toBe("was 3");
    expect(describeOrigin(corrected("EUR", null)).was).toBe("was EUR");
    expect(describeOrigin(corrected(null, null)).was).toBe("was empty");
    expect(describeOrigin(corrected(["PO-2026-0001", "PO-2026-0002"], "read")).was).toBe("was PO-2026-0001, PO-2026-0002");
    expect(describeOrigin(corrected(1, null)).was).toBe("was 1");
  });
});

describe("a change in plain language", () => {
  test("line and header changes", () => {
    expect(
      describeChange({ scope: "line", line_number: 1, field: "quantity", before: { value: null, status: "illegible" }, after: { value: "24", status: "read" } }),
    ).toBe("Line 1 quantity: illegible → 24");
    expect(
      describeChange({ scope: "header", line_number: null, field: "currency", before: { value: "EUR", status: null }, after: { value: "USD", status: null } }),
    ).toBe("Currency: EUR → USD");
    expect(
      describeChange({ scope: "header", field: "po_references", before: { value: [], status: "illegible" }, after: { value: ["PO-2026-0002"], status: "read" } }),
    ).toBe("Purchase orders: illegible → PO-2026-0002");
    expect(
      describeChange({ scope: "line", line_number: 2, field: "unit_price", before: { value: "9.90", status: "read" }, after: { value: null, status: "absent" } }),
    ).toBe("Line 2 unit price: 9.90 → not on the document");
    expect(
      describeChange({ scope: "document", field: "document_type", before: { value: "quotation" }, after: { value: "invoice" } }),
    ).toBe("Document type: quotation → invoice");
  });

  test("an empty value says why it is empty", () => {
    expect(describeState({ value: null, status: "illegible" })).toBe("illegible");
    expect(describeState({ value: null, status: "absent" })).toBe("not on the document");
    expect(describeState({ value: null, status: null })).toBe("empty");
    expect(describeState({ value: "", status: null })).toBe("empty");
    expect(describeState(undefined)).toBe("empty");
  });
});

describe("audit events in plain language", () => {
  test("a correction lists each change, then the comment; versions go to the technical line", () => {
    const text = describeAuditEvent(
      event("EXTRACTION_CORRECTED", {
        extraction_version: 2,
        previous_version: 1,
        comment: "Read from the paper copy.",
        corrections: { lines: [{ line_number: 1, quantity: "24" }] },
        changes: [
          { scope: "line", line_number: 1, field: "quantity", before: { value: null, status: "illegible" }, after: { value: "24", status: "read" } },
        ],
      }),
    );
    expect(text.title).toBe("Data corrected");
    expect(text.lines).toEqual(["Line 1 quantity: illegible → 24", "Comment: Read from the paper copy."]);
    expect(text.technical).toBe("from version 1 · to version 2");
    expect(text.lines.join(" ")).not.toMatch(/[{}\[\]"]/); // no raw JSON in the readable part
  });

  test("an evaluation gives the outcome by name and each rule with its message", () => {
    const text = describeAuditEvent(
      event("INVOICE_EVALUATED", {
        evaluation_id: 7,
        extraction_version: 1,
        outcome: "HELD_FOR_REVIEW",
        reason_codes: ["H2"],
        reasons: [{ code: "H2", message: "Line 1: unit price 9.90 differs from PO price 9.00 by 10.00%, beyond 2.00% tolerance" }],
        parameters_version: 3,
      }),
    );
    expect(text.lines).toEqual([
      "Outcome: Held for review",
      "H2: Line 1: unit price 9.90 differs from PO price 9.00 by 10.00%, beyond 2.00% tolerance",
    ]);
    expect(text.technical).toBe("evaluation 7 · extraction version 1 · parameters version 3");
  });

  test("an older evaluation event with only rule codes still reads as text", () => {
    const text = describeAuditEvent(event("INVOICE_EVALUATED", { outcome: "REJECTED", reason_codes: ["R4", "A8"] }));
    expect(text.lines).toEqual([
      "Outcome: Rejected",
      "R4: Duplicate of an invoice already processed",
      "A8: Cumulative invoiced quantity exceeds received",
    ]);
  });

  test("an evaluation with no rules says so", () => {
    expect(describeAuditEvent(event("INVOICE_EVALUATED", { outcome: "AUTO_PAY", reason_codes: [], reasons: [] })).lines).toEqual([
      "Outcome: Auto-Pay",
      "No rule fired.",
    ]);
  });

  test("a decision: what was decided, the status change, the comment and the role required", () => {
    const text = describeAuditEvent(
      event("HUMAN_DECISION_RECORDED", {
        human_decision_id: 4,
        decision: "APPROVE",
        previous_outcome: "HELD_FOR_REVIEW",
        new_status: "APPROVED_FOR_PAYMENT",
        evaluation_id: 7,
        required_role: "AP_MANAGER",
        comment: "Price confirmed.",
        supersedes_decision_ids: [3],
      }),
    );
    expect(text.lines).toEqual([
      "Decision: Approve for payment",
      "Status: Held for review → Approved for payment",
      "Comment: Price confirmed.",
      "Role required: AP Manager",
    ]);
    expect(text.technical).toBe("decision 4 · evaluation 7 · supersedes [3]");
  });

  test("the other events", () => {
    expect(describeAuditEvent(event("INVOICE_SUBMITTED", { filename: "invoice.pdf", file_sha256: "abc123" }))).toEqual({
      title: "Document uploaded",
      lines: ["File: invoice.pdf"],
      technical: "sha256 abc123",
    });
    const read = describeAuditEvent(
      event("EXTRACTION_SUCCEEDED", { extraction_version: 1, document_type: "invoice", from_cache: false, model: "some-deployment", prompt_version: "2", tokens: { total_tokens: 2500 } }),
    );
    expect(read.lines).toEqual(["Document type: invoice", "Read by the model."]);
    expect(read.lines.join(" ")).not.toContain("some-deployment"); // the deployment name is technical
    expect(read.technical).toContain("model some-deployment");
    expect(describeAuditEvent(event("EXTRACTION_SUCCEEDED", { document_type: "invoice", from_cache: true })).lines[1]).toBe(
      "Taken from an earlier reading of the same file.",
    );
    expect(describeAuditEvent(event("EXTRACTION_FAILED", { error_code: "PROCESSING_ERROR", errors: ["The document could not be processed."] }))).toEqual({
      title: "Reading failed",
      lines: ["The document could not be processed."],
      technical: "code PROCESSING_ERROR",
    });
    expect(describeAuditEvent(event("HELD_AFTER_CORRECTION", { engine_outcome: "AUTO_PAY", extraction_version: 2 })).lines).toEqual([
      "Correcting data does not approve payment: the invoice waits for a decision.",
      "Rules result for the corrected data: Auto-Pay",
    ]);
    expect(describeAuditEvent(event("DECISION_DENIED", { decision: "APPROVE", status: "HELD_FOR_REVIEW", reason: "above the analyst approval limit" })).lines).toEqual([
      "Attempted: Approve for payment",
      "Refused because: above the analyst approval limit",
      "Status at that moment: Held for review",
    ]);
    expect(
      describeAuditEvent(event("HUMAN_DECISION_SUPERSEDED", { human_decision_ids: [3], previous_status: "REJECTED_BY_REVIEWER", reason: "NEW_DECISION" })).lines,
    ).toEqual(["An earlier decision is no longer in force: a later decision replaced it.", "Status at that moment: Rejected by reviewer"]);
    expect(describeAuditEvent(event("CLASSIFIED_NOT_AN_INVOICE", { document_type: "quotation", classification_reason: "Titled Quotation." })).lines).toEqual([
      "The document is a quotation, not an invoice.",
      "Titled Quotation.",
    ]);
  });

  test("an event type this version does not know is still shown, with its details as technical text", () => {
    const text = describeAuditEvent(event("SOMETHING_NEW", { a: 1 }));
    expect(text.title).toBe("something new");
    expect(text.technical).toBe("a 1");
  });
});

describe("labels", () => {
  test("every rule has a one-line description", () => {
    const codes = ["R1", "R2", "R3", "R4", "R5", "H1", "H2", "H3", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10"];
    for (const code of codes) expect(RULE_DESCRIPTIONS[code]?.length).toBeGreaterThan(10);
  });

  test("where a rule comes from: specification, confirmed by the client, or assumption", () => {
    expect(RULE_SOURCE_LABELS).toEqual({ SPEC: "Specification", CONFIRMED: "Confirmed by client", ASSUMPTION: "Assumption" });
  });

  test("supplier: the legal name with its code, falling back to what is known", () => {
    expect(supplierLabel("Test Supplier B", "SUP-002")).toBe("Test Supplier B (SUP-002)");
    expect(supplierLabel(null, "SUP-002")).toBe("SUP-002");
    expect(supplierLabel(undefined, null)).toBe("–");
  });

  test("file sizes", () => {
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(2411)).toBe("2.4 KB");
    expect(formatFileSize(9437188)).toBe("9.0 MB");
  });

  test("the API's advice on a decision is carried through without disabling it", () => {
    const [approve, reject] = decisionChoices({
      decisions: [
        { decision: "APPROVE", allowed: true, required_role: "AP_ANALYST", notice: null, warning: "No purchase order is associated with this invoice." },
        { decision: "REJECT", allowed: true, required_role: "AP_ANALYST", notice: null },
      ],
      can_correct: true,
      can_retry: false,
    });
    expect(approve.enabled).toBe(true);
    expect(approve.warning).toBe("No purchase order is associated with this invoice.");
    expect(reject.warning).toBeNull();
  });
});
