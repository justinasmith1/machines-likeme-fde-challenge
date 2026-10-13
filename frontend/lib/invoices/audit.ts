// The audit trail in plain language. Each event becomes a title, a few sentences a person
// can read, and one secondary line with the technical identifiers (ids, versions, hashes).

import type { AuditEvent } from "@/lib/api/types";
import { RULE_DESCRIPTIONS } from "./actions";
import { presentStatus } from "./status";

export interface AuditText {
  title: string;
  lines: string[];
  /** Ids, versions and hashes. Shown smaller, after the readable lines. */
  technical: string;
}

const TITLES: Record<string, string> = {
  INVOICE_SUBMITTED: "Document uploaded",
  EXTRACTION_SUCCEEDED: "Document read",
  EXTRACTION_FAILED: "Reading failed",
  EXTRACTION_RETRY_REQUESTED: "Retry requested",
  CLASSIFIED_NOT_AN_INVOICE: "Classified as not an invoice",
  INVOICE_EVALUATED: "Rules evaluated",
  EXTRACTION_CORRECTED: "Data corrected",
  HELD_AFTER_CORRECTION: "Held after correction",
  HUMAN_DECISION_RECORDED: "Decision recorded",
  HUMAN_DECISION_SUPERSEDED: "Earlier decision superseded",
  DECISION_DENIED: "Decision attempt refused",
};

const DECISIONS: Record<string, string> = {
  APPROVE: "Approve for payment",
  OVERRIDE: "Override and approve",
  REJECT: "Reject",
  REQUEST_CREDIT_NOTE: "Request credit note",
};

const ROLES: Record<string, string> = {
  INTAKE: "Intake",
  AP_ANALYST: "AP Analyst",
  AP_MANAGER: "AP Manager",
};

const FIELD_LABELS: Record<string, string> = {
  document_type: "Document type",
  supplier_name: "Supplier name",
  supplier_tax_id: "Supplier tax ID",
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  po_references: "Purchase orders",
  currency: "Currency",
  subtotal: "Subtotal",
  tax_amount: "Tax",
  total_amount: "Total",
  description: "description",
  quantity: "quantity",
  unit_price: "unit price",
  line_total: "line total",
  po_line_reference: "PO line",
};

const SUPERSEDED: Record<string, string> = {
  NEW_DECISION: "a later decision replaced it",
  EXTRACTION_CORRECTED: "the data was corrected afterwards",
};

type Details = Record<string, unknown>;
interface FieldState {
  value?: unknown;
  status?: unknown;
}
interface Change {
  scope?: string;
  line_number?: number | null;
  field?: string;
  before?: FieldState;
  after?: FieldState;
}

const text = (value: unknown): string => (value === null || value === undefined ? "" : String(value));
const status = (value: unknown): string => presentStatus(text(value)).label;

/** One side of a change: "illegible", "not on the document", "empty", or the value. */
export function describeState(state: FieldState | undefined): string {
  const value = state?.value;
  const empty = value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
  if (empty) {
    if (state?.status === "illegible") return "illegible";
    if (state?.status === "absent") return "not on the document";
    return "empty";
  }
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** "Line 1 quantity: illegible → 24", "Currency: EUR → USD". */
export function describeChange(change: Change): string {
  const field = FIELD_LABELS[change.field ?? ""] ?? text(change.field).replaceAll("_", " ");
  const where = change.scope === "line" ? `Line ${change.line_number} ${field}` : field;
  return `${where}: ${describeState(change.before)} → ${describeState(change.after)}`;
}

function technical(details: Details, keys: Array<[string, string]>): string {
  return keys
    .filter(([key]) => details[key] !== null && details[key] !== undefined && details[key] !== "")
    .map(([key, label]) => {
      const value = details[key];
      return `${label} ${typeof value === "object" ? JSON.stringify(value) : String(value)}`;
    })
    .join(" · ");
}

function reasonLines(details: Details): string[] {
  const reasons = details.reasons as Array<{ code?: string; message?: string }> | undefined;
  if (Array.isArray(reasons)) {
    return reasons.map((reason) => `${reason.code}: ${reason.message ?? RULE_DESCRIPTIONS[reason.code ?? ""] ?? ""}`);
  }
  // Events recorded before messages were stored only carry the codes.
  const codes = (details.reason_codes as string[] | undefined) ?? [];
  return codes.map((code) => `${code}: ${RULE_DESCRIPTIONS[code] ?? "rule fired"}`);
}

export function describeAuditEvent(event: AuditEvent): AuditText {
  const d = event.details ?? {};
  const title = TITLES[event.event_type] ?? event.event_type.replaceAll("_", " ").toLowerCase();

  switch (event.event_type) {
    case "INVOICE_SUBMITTED":
      return {
        title,
        lines: [`File: ${text(d.filename)}`],
        technical: technical(d, [["file_sha256", "sha256"]]),
      };

    case "EXTRACTION_SUCCEEDED":
      return {
        title,
        lines: [
          `Document type: ${text(d.document_type)}`,
          d.from_cache ? "Taken from an earlier reading of the same file." : "Read by the model.",
        ],
        technical: technical(d, [
          ["extraction_version", "extraction version"],
          ["model", "model"],
          ["prompt_version", "prompt version"],
          ["tokens", "tokens"],
        ]),
      };

    case "EXTRACTION_FAILED":
      return {
        title,
        lines: ((d.errors as string[] | undefined) ?? []).map(String),
        technical: technical(d, [
          ["error_code", "code"],
          ["error_type", "error type"],
        ]),
      };

    case "EXTRACTION_RETRY_REQUESTED":
      return {
        title,
        lines: d.previous_error ? [`Previous problem: ${text(d.previous_error)}`] : [],
        technical: "",
      };

    case "CLASSIFIED_NOT_AN_INVOICE":
      return {
        title,
        lines: [`The document is a ${text(d.document_type)}, not an invoice.`, text(d.classification_reason)].filter(Boolean),
        technical: technical(d, [["extraction_version", "extraction version"]]),
      };

    case "INVOICE_EVALUATED": {
      const reasons = reasonLines(d);
      return {
        title,
        lines: [`Outcome: ${status(d.outcome)}`, ...(reasons.length > 0 ? reasons : ["No rule fired."])],
        technical: technical(d, [
          ["evaluation_id", "evaluation"],
          ["extraction_version", "extraction version"],
          ["parameters_version", "parameters version"],
        ]),
      };
    }

    case "EXTRACTION_CORRECTED": {
      const changes = (d.changes as Change[] | undefined) ?? [];
      return {
        title,
        lines: [
          ...(changes.length > 0 ? changes.map(describeChange) : ["The extracted data was changed."]),
          ...(d.comment ? [`Comment: ${text(d.comment)}`] : []),
        ],
        technical: technical(d, [
          ["previous_version", "from version"],
          ["extraction_version", "to version"],
        ]),
      };
    }

    case "HELD_AFTER_CORRECTION":
      return {
        title,
        lines: [
          "Correcting data does not approve payment: the invoice waits for a decision.",
          `Rules result for the corrected data: ${status(d.engine_outcome)}`,
        ],
        technical: technical(d, [["extraction_version", "extraction version"]]),
      };

    case "HUMAN_DECISION_RECORDED":
      return {
        title,
        lines: [
          `Decision: ${DECISIONS[text(d.decision)] ?? text(d.decision)}`,
          `Status: ${status(d.previous_outcome)} → ${status(d.new_status)}`,
          ...(d.comment ? [`Comment: ${text(d.comment)}`] : []),
          `Role required: ${ROLES[text(d.required_role)] ?? text(d.required_role)}`,
        ],
        technical: technical(d, [
          ["human_decision_id", "decision"],
          ["evaluation_id", "evaluation"],
          ["supersedes_decision_ids", "supersedes"],
        ]),
      };

    case "HUMAN_DECISION_SUPERSEDED":
      return {
        title,
        lines: [
          `An earlier decision is no longer in force: ${SUPERSEDED[text(d.reason)] ?? text(d.reason)}.`,
          `Status at that moment: ${status(d.previous_status)}`,
        ],
        technical: technical(d, [["human_decision_ids", "decisions"]]),
      };

    case "DECISION_DENIED":
      return {
        title,
        lines: [
          `Attempted: ${DECISIONS[text(d.decision)] ?? text(d.decision)}`,
          `Refused because: ${text(d.reason)}`,
          `Status at that moment: ${status(d.status)}`,
        ],
        technical: "",
      };

    default:
      return {
        title,
        lines: [],
        technical: Object.entries(d)
          .filter(([, value]) => value !== null && value !== undefined && value !== "")
          .map(([key, value]) => `${key.replaceAll("_", " ")} ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
          .join(" · "),
      };
  }
}
