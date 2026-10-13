// An in-memory stand-in for the backend, used by the screen tests. It follows the same
// rules the real API enforces for the cases the tests exercise: role views, the approval
// limit, corrections that leave the invoice held, and decisions that change the status.

import { vi } from "vitest";
import type { ApiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type {
  Actions,
  AuditEvent,
  DecisionName,
  Evaluation,
  Extraction,
  IntakeInvoice,
  InvoiceStatus,
  ReviewerInvoice,
  Role,
  RuleParameters,
} from "@/lib/api/types";

export const LIMIT_NOTICE =
  "The invoice total (6,000.00) is above the analyst approval limit (5,000.00). The AP Manager must decide.";
export const NO_PO_WARNING =
  "No purchase order is associated with this invoice: none of its lines is matched to a purchase order line. Consider correcting the purchase order number first.";
export const CORRECTED_REASON =
  "Extracted data was corrected by ana.analyst; a human decision is required before payment.";

const STATUS_AFTER: Record<DecisionName, InvoiceStatus> = {
  APPROVE: "APPROVED_FOR_PAYMENT",
  OVERRIDE: "OVERRIDDEN",
  REJECT: "REJECTED_BY_REVIEWER",
  REQUEST_CREDIT_NOTE: "CREDIT_NOTE_REQUESTED",
};

export function heldExtraction(): Extraction {
  return {
    version: 1,
    source: "MODEL",
    created_by: "system",
    created_at: "2026-10-12T09:01:00+00:00",
    comment: null,
    model: "test-deployment",
    prompt_version: "2",
    document_type: "invoice",
    classification_reason: "Requests payment for delivered goods.",
    header: {
      supplier_name: "Test Supplier B",
      supplier_tax_id: "TAX-B",
      invoice_number: { value: "TEST-0001", status: "read", origin: { source: "MODEL" }, suggestion: null },
      invoice_date: { value: "2026-02-15", status: "read", origin: { source: "MODEL" }, suggestion: null },
      due_date: "2026-03-15",
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
        quantity: {
          value: null,
          status: "illegible",
          origin: { source: "MODEL" },
          suggestion: {
            value: "4",
            is_suggestion: true,
            note: "Suggested by the model. It is not a value read from the document and the rules engine does not use it.",
          },
        },
        unit_price: { value: "25.00", status: "read", origin: { source: "MODEL" }, suggestion: null },
        line_total: "100.00",
        po_line_printed_on_document: null,
        po_line_mapping: { po_number: "PO-2026-0002", po_line_number: 1, method: "DESCRIPTION" },
      },
    ],
  };
}

interface FakeOptions {
  role: Role;
  username?: string;
  /** Total of the invoice the fake holds. Above 5000 the analyst may not release payment. */
  total?: string;
  /** No line is matched to a purchase order: the API adds its advice to payment decisions. */
  noPurchaseOrder?: boolean;
}

export function createFakeApi(options: FakeOptions) {
  const role = options.role;
  const username = options.username ?? "ana.analyst";
  const total = options.total ?? "121.00";
  const aboveLimit = Number(total) > 5000;

  const state = {
    status: "HELD_FOR_REVIEW" as InvoiceStatus,
    statusReason: null as string | null,
    engineOutcome: "HELD_FOR_REVIEW",
    extraction: heldExtraction(),
    versions: [heldExtraction()] as Extraction[],
    decisions: [] as ReviewerInvoice["decisions"],
    /** Earlier runs of the engine, oldest first. The current one is built in detail(). */
    earlierEvaluations: [] as Evaluation[],
    intake: [] as IntakeInvoice[],
    pendingPolls: 0,
    audit: [
      { id: 1, invoice_id: 1, event_type: "INVOICE_SUBMITTED", actor: "ines.intake", role: "INTAKE", timestamp: "2026-10-12T09:00:00+00:00", details: { filename: "invoice.pdf" } },
      { id: 2, invoice_id: 1, event_type: "EXTRACTION_SUCCEEDED", actor: "system", role: "SYSTEM", timestamp: "2026-10-12T09:00:30+00:00", details: { extraction_version: 1, document_type: "invoice", from_cache: false, model: "test-deployment", prompt_version: "2", tokens: { total_tokens: 2500 } } },
      { id: 3, invoice_id: 1, event_type: "INVOICE_EVALUATED", actor: "system", role: "SYSTEM", timestamp: "2026-10-12T09:01:00+00:00", details: { evaluation_id: 1, extraction_version: 1, outcome: "HELD_FOR_REVIEW", reason_codes: ["A6"], reasons: [{ code: "A6", message: "Line 1: quantity is illegible; the system does not infer it" }], parameters_version: 1 } },
    ] as AuditEvent[],
    parameters: {
      version: 1,
      price_tolerance_pct: "0.02",
      analyst_approval_limit: "5000.00",
      rounding_tolerance: "0.01",
      enabled_assumption_rules: ["A1", "A2", "A3", "A4", "A5", "A8", "A10"],
      changed_by: "system",
      changed_at: "2026-10-12T08:00:00+00:00",
      comment: "Initial values from configuration.",
    } as RuleParameters,
    history: [] as RuleParameters[],
  };
  state.history = [state.parameters];

  function actions(): Actions {
    if (role === "INTAKE") return { decisions: [], can_correct: false, can_retry: false };
    const payable = ["AUTO_PAY", "APPROVED_FOR_PAYMENT", "OVERRIDDEN"].includes(state.status);
    const decisions: Actions["decisions"] = [];
    if (!payable) {
      decisions.push({
        decision: "APPROVE",
        allowed: !aboveLimit || role === "AP_MANAGER",
        required_role: aboveLimit ? "AP_MANAGER" : "AP_ANALYST",
        notice: aboveLimit ? LIMIT_NOTICE : null,
        warning: options.noPurchaseOrder ? NO_PO_WARNING : null,
      });
    }
    if (state.status !== "REJECTED_BY_REVIEWER") {
      decisions.push({ decision: "REJECT", allowed: true, required_role: "AP_ANALYST", notice: null });
    }
    if (state.status !== "CREDIT_NOTE_REQUESTED") {
      decisions.push({ decision: "REQUEST_CREDIT_NOTE", allowed: true, required_role: "AP_ANALYST", notice: null });
    }
    return { decisions, can_correct: true, can_retry: false };
  }

  function currentEvaluation(): Evaluation {
    return {
      id: state.versions.length,
      extraction_version: state.versions.length,
      outcome: state.engineOutcome,
      evaluated_at: "2026-10-12T09:01:00+00:00",
      reasons:
        state.engineOutcome === "AUTO_PAY"
          ? []
          : [
              {
                code: "A6",
                message: "Line 1: quantity is illegible; the system does not infer it",
                outcome: "HELD_FOR_REVIEW",
                source: "CONFIRMED",
                decision_log_ref: "DL-18",
                line_number: 1,
              },
            ],
      parameters_snapshot: { version: 1 },
      reference_data_version: {},
      matching: {
        supplier_id: "SUP-002",
        supplier_method: "TAX_ID",
        po_references: ["PO-2026-0002"],
        missing_po_numbers: [],
        flags: [],
      },
    };
  }

  function detail(): ReviewerInvoice {
    return {
      invoice: {
        id: 1,
        filename: "invoice.pdf",
        uploaded_by: "ines.intake",
        uploaded_at: "2026-10-12T09:00:00+00:00",
        status: state.status,
        status_reason: state.statusReason,
        supplier_id: "SUP-002",
        supplier_name: "Test Supplier B",
        invoice_number: "TEST-0001",
        total,
        currency: "EUR",
      },
      actions: actions(),
      extraction: state.extraction,
      evaluation: currentEvaluation(),
      evaluation_history: [...state.earlierEvaluations, currentEvaluation()],
      decisions: state.decisions,
      extraction_versions: state.versions,
    };
  }

  const api = {
    me: vi.fn(async () => ({ username, role, upload: { max_mb: 10, extensions: [".pdf", ".png", ".jpg", ".jpeg"] } })),

    listInvoices: vi.fn(async () => {
      if (role !== "INTAKE") return [detail().invoice];
      // A pending document becomes held after the configured number of polls.
      state.intake = state.intake.map((invoice) => {
        if (invoice.status !== "PENDING_EXTRACTION") return invoice;
        if (state.pendingPolls > 0) {
          state.pendingPolls -= 1;
          return invoice;
        }
        return {
          ...invoice,
          status: "HELD_FOR_REVIEW" as InvoiceStatus,
          reasons: ["Line 1: unit price 30.00 differs from PO price 25.00 by 20.00%, beyond 2.00% tolerance"],
        };
      });
      return state.intake;
    }),

    getInvoice: vi.fn(async () => detail()),
    getDocument: vi.fn(async () => new Blob(["%PDF-1.7"], { type: "application/pdf" })),

    getAudit: vi.fn(async () => {
      if (role !== "AP_MANAGER") throw new ApiError(403, "PERMISSION_DENIED", "This action requires the role AP_MANAGER.");
      return state.audit;
    }),

    uploadInvoice: vi.fn(async (file: File) => {
      const invoice: IntakeInvoice = {
        id: state.intake.length + 1,
        filename: file.name,
        uploaded_at: "2026-10-12T09:00:00+00:00",
        status: "PENDING_EXTRACTION",
        reasons: [],
      };
      state.intake = [invoice, ...state.intake];
      state.pendingPolls = 1;
      return { id: invoice.id, filename: invoice.filename, status: invoice.status };
    }),

    retryExtraction: vi.fn(async () => ({ id: 1, filename: "invoice.pdf", status: "PENDING_EXTRACTION" as InvoiceStatus })),

    correctExtraction: vi.fn(async (_id: number, corrections: Record<string, unknown>, comment: string) => {
      const next = structuredClone(state.extraction);
      const lines = (corrections.lines as Array<{ line_number: number; quantity?: string }> | undefined) ?? [];
      state.earlierEvaluations = [...state.earlierEvaluations, currentEvaluation()];
      for (const change of lines) {
        if (change.quantity !== undefined && next.lines) {
          const before = next.lines[change.line_number - 1].quantity;
          next.lines[change.line_number - 1].quantity = {
            value: change.quantity,
            status: "read",
            // Typed by a person: the API says who, and what the field was before.
            origin: {
              source: "HUMAN_CORRECTION",
              corrected_by: username,
              corrected_at: "2026-10-12T09:30:00+00:00",
              version: state.versions.length + 1,
              previous_value: before.value,
              previous_status: before.status,
            },
            suggestion: null,
          };
          state.audit = [
            ...state.audit,
            {
              id: state.audit.length + 1,
              invoice_id: 1,
              event_type: "EXTRACTION_CORRECTED",
              actor: username,
              role,
              timestamp: "2026-10-12T09:30:00+00:00",
              details: {
                extraction_version: state.versions.length + 1,
                previous_version: state.versions.length,
                comment,
                changes: [
                  {
                    scope: "line",
                    line_number: change.line_number,
                    field: "quantity",
                    before: { value: before.value, status: before.status },
                    after: { value: change.quantity, status: "read" },
                  },
                ],
              },
            },
          ];
        }
      }
      next.version = state.versions.length + 1;
      next.source = "HUMAN_CORRECTION";
      next.created_by = username;
      next.comment = comment;
      state.extraction = next;
      state.versions = [...state.versions, next];
      state.engineOutcome = "AUTO_PAY";
      // DL-46: correcting never approves payment.
      state.status = "HELD_FOR_REVIEW";
      state.statusReason = CORRECTED_REASON;
      return detail();
    }),

    decide: vi.fn(async (_id: number, decision: DecisionName, comment: string) => {
      if (role === "INTAKE") {
        throw new ApiError(403, "PERMISSION_DENIED", "Only an AP Analyst or the AP Manager can decide on an invoice.");
      }
      if ((decision === "APPROVE" || decision === "OVERRIDE") && aboveLimit && role !== "AP_MANAGER") {
        throw new ApiError(403, "PERMISSION_DENIED", LIMIT_NOTICE);
      }
      const previous = state.status;
      state.decisions = [
        ...state.decisions.map((d) => ({ ...d, superseded: true, superseded_reason: "NEW_DECISION" })),
        {
          id: state.decisions.length + 1,
          decision,
          previous_status: previous,
          comment,
          decided_by: username,
          role,
          required_role: aboveLimit && decision === "APPROVE" ? "AP_MANAGER" : "AP_ANALYST",
          decided_at: "2026-10-12T10:00:00+00:00",
          superseded: false,
          superseded_at: null,
          superseded_reason: null,
        },
      ];
      state.status = STATUS_AFTER[decision];
      state.statusReason = null;
      return detail();
    }),

    reviewQueue: vi.fn(async () => (state.status === "HELD_FOR_REVIEW" ? [detail().invoice] : [])),
    getParameters: vi.fn(async () => state.parameters),

    updateParameters: vi.fn(async (values: Record<string, unknown>, comment: string) => {
      if (role !== "AP_MANAGER") throw new ApiError(403, "PERMISSION_DENIED", "This action requires the role AP_MANAGER.");
      state.parameters = {
        ...state.parameters,
        ...(values as Partial<RuleParameters>),
        version: state.parameters.version + 1,
        changed_by: username,
        comment,
      };
      state.history = [state.parameters, ...state.history];
      return state.parameters;
    }),

    parameterHistory: vi.fn(async () => state.history),
  };

  return { api: api as unknown as ApiClient, calls: api, state };
}
