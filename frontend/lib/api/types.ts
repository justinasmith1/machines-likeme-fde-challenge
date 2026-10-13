// Shapes of the backend API responses. See the "API" section of the README.

export type Role = "INTAKE" | "AP_ANALYST" | "AP_MANAGER";

export type InvoiceStatus =
  | "PENDING_EXTRACTION"
  | "EXTRACTION_FAILED"
  | "NOT_AN_INVOICE"
  | "AUTO_PAY"
  | "HELD_FOR_REVIEW"
  | "REJECTED"
  | "APPROVED_FOR_PAYMENT"
  | "OVERRIDDEN"
  | "REJECTED_BY_REVIEWER"
  | "CREDIT_NOTE_REQUESTED";

export type DecisionName = "APPROVE" | "OVERRIDE" | "REJECT" | "REQUEST_CREDIT_NOTE";
export type ReadStatus = "read" | "illegible" | "absent";

export interface Me {
  username: string;
  role: Role;
  upload: { max_mb: number; extensions: string[] };
}

/** What an intake user gets: nothing internal. */
export interface IntakeInvoice {
  id: number;
  filename: string;
  uploaded_at: string;
  status: InvoiceStatus;
  reasons: string[];
}

export interface InvoiceSummary {
  id: number;
  filename: string;
  uploaded_by: string;
  uploaded_at: string;
  status: InvoiceStatus;
  status_reason: string | null;
  supplier_id: string | null;
  /** Legal name from the supplier master; null when unresolved. */
  supplier_name?: string | null;
  invoice_number: string | null;
  total: string | null;
  currency: string | null;
}

/** A value the model inferred. Never a value read from the document (DL-18). */
export interface Suggestion {
  value: string;
  is_suggestion: true;
  note: string;
}

/** Who last set a field: the model, or a person in a correction. */
export type FieldOrigin =
  | { source: "MODEL" }
  | {
      source: "HUMAN_CORRECTION";
      corrected_by: string;
      corrected_at: string | null;
      version: number;
      previous_value: string | number | string[] | null;
      previous_status: ReadStatus | null;
    };

export interface ReadField {
  value: string | null;
  status: ReadStatus | null;
  origin?: FieldOrigin;
  suggestion: Suggestion | null;
}

export interface PoReferences {
  values: string[];
  status: ReadStatus | null;
  origin?: FieldOrigin;
  suggestion: { values: string[]; is_suggestion: true; note: string } | null;
}

export interface LineMapping {
  po_number: string | null;
  po_line_number: number | null;
  method: string;
}

export interface ExtractionLine {
  line_number: number;
  description: string | null;
  quantity: ReadField;
  unit_price: ReadField;
  line_total: string | null;
  po_line_printed_on_document: number | null;
  po_line_mapping: LineMapping | null;
  /** Fields without a reading status that a person corrected. */
  corrected_fields?: Record<string, FieldOrigin>;
}

export interface ExtractionHeader {
  supplier_name: string | null;
  supplier_tax_id: string | null;
  invoice_number: ReadField;
  invoice_date: ReadField;
  due_date: string | null;
  po_references: PoReferences;
  currency: string | null;
  subtotal: string | null;
  tax_amount: string | null;
  total_amount: string | null;
}

export interface ExtractionVersion {
  version: number;
  source: "MODEL" | "HUMAN_CORRECTION";
  created_by: string;
  created_at: string;
  comment: string | null;
  model: string | null;
  prompt_version: string | null;
}

export interface Extraction extends ExtractionVersion {
  document_type?: string;
  classification_reason?: string;
  /** Header fields without a reading status that a person corrected. */
  corrected_fields?: Record<string, FieldOrigin>;
  header?: ExtractionHeader;
  lines?: ExtractionLine[];
}

export interface Reason {
  code: string;
  message: string;
  outcome: string;
  source: "SPEC" | "CONFIRMED" | "ASSUMPTION";
  decision_log_ref: string | null;
  line_number: number | null;
}

export interface Evaluation {
  id: number;
  /** The extraction version this run of the engine looked at. */
  extraction_version?: number | null;
  outcome: string;
  evaluated_at: string;
  reasons: Reason[];
  parameters_snapshot: Record<string, unknown>;
  reference_data_version: Record<string, string>;
  matching: {
    supplier_id: string | null;
    supplier_method: string | null;
    po_references: string[];
    missing_po_numbers: string[];
    flags: string[];
  };
}

export interface DecisionRecord {
  id: number;
  decision: DecisionName;
  previous_status: string;
  comment: string;
  decided_by: string;
  role: string;
  required_role: string;
  decided_at: string;
  superseded: boolean;
  superseded_at: string | null;
  superseded_reason: string | null;
}

/** One decision that applies to the invoice, as computed by the backend for this user. */
export interface DecisionOption {
  decision: DecisionName;
  allowed: boolean;
  required_role: Role;
  notice: string | null;
  /** Advice from the API that does not block the decision. */
  warning?: string | null;
}

export interface Actions {
  decisions: DecisionOption[];
  can_correct: boolean;
  can_retry: boolean;
}

export interface ReviewerInvoice {
  invoice: InvoiceSummary;
  actions: Actions;
  extraction: Extraction | null;
  evaluation: Evaluation | null;
  /** Every run of the engine, oldest first; the last one is the one in force. */
  evaluation_history?: Evaluation[];
  decisions: DecisionRecord[];
  extraction_versions: ExtractionVersion[];
}

export type InvoiceDetail = ReviewerInvoice | IntakeInvoice;

export function isReviewerDetail(detail: InvoiceDetail): detail is ReviewerInvoice {
  return "invoice" in detail;
}

export interface AuditEvent {
  id: number;
  invoice_id: number | null;
  event_type: string;
  actor: string;
  role: string;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface RuleParameters {
  version: number;
  price_tolerance_pct: string;
  analyst_approval_limit: string;
  rounding_tolerance: string;
  enabled_assumption_rules: string[];
  changed_by: string | null;
  changed_at: string | null;
  comment?: string | null;
}

export interface UploadReceipt {
  id: number;
  filename: string;
  status: InvoiceStatus;
}

export type Corrections = {
  document_type?: string;
  header?: Record<string, string | string[] | null>;
  lines?: Array<{ line_number: number } & Record<string, string | number | null>>;
};
