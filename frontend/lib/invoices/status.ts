// How each invoice status is shown. A status always has a readable name and a symbol, so
// colour is never the only signal.

import type { InvoiceStatus } from "@/lib/api/types";

export type Tone = "neutral" | "progress" | "success" | "warning" | "danger";

export interface StatusPresentation {
  label: string;
  tone: Tone;
  symbol: string;
  /** One sentence for a person who does not know the process. */
  description: string;
}

const PRESENTATION: Record<InvoiceStatus, StatusPresentation> = {
  PENDING_EXTRACTION: {
    label: "Processing",
    tone: "progress",
    symbol: "…",
    description: "The document is being read. This page updates by itself.",
  },
  EXTRACTION_FAILED: {
    label: "Could not be read",
    tone: "danger",
    symbol: "!",
    description: "The document could not be processed. Accounts Payable can retry it.",
  },
  NOT_AN_INVOICE: {
    label: "Not an invoice",
    tone: "neutral",
    symbol: "–",
    description: "The document was received but is not an invoice, so it will not be paid.",
  },
  AUTO_PAY: {
    label: "Auto-Pay",
    tone: "success",
    symbol: "✓",
    description: "The invoice matched its purchase order and receipts and is approved for payment.",
  },
  HELD_FOR_REVIEW: {
    label: "Held for review",
    tone: "warning",
    symbol: "⏸",
    description: "A person in Accounts Payable has to review the invoice before it can be paid.",
  },
  REJECTED: {
    label: "Rejected",
    tone: "danger",
    symbol: "✕",
    description: "The invoice was rejected automatically.",
  },
  APPROVED_FOR_PAYMENT: {
    label: "Approved for payment",
    tone: "success",
    symbol: "✓",
    description: "A reviewer approved the invoice for payment.",
  },
  OVERRIDDEN: {
    label: "Approved by override",
    tone: "success",
    symbol: "✓",
    description: "A reviewer approved the invoice for payment although it had been rejected automatically.",
  },
  REJECTED_BY_REVIEWER: {
    label: "Rejected by reviewer",
    tone: "danger",
    symbol: "✕",
    description: "A reviewer rejected the invoice.",
  },
  CREDIT_NOTE_REQUESTED: {
    label: "Credit note requested",
    tone: "warning",
    symbol: "↩",
    description: "A reviewer asked the supplier for a credit note.",
  },
};

const UNKNOWN: StatusPresentation = {
  label: "Unknown status",
  tone: "neutral",
  symbol: "?",
  description: "",
};

export function presentStatus(status: string): StatusPresentation {
  return PRESENTATION[status as InvoiceStatus] ?? { ...UNKNOWN, label: status };
}

export const ALL_STATUSES = Object.keys(PRESENTATION) as InvoiceStatus[];

export function isPending(status: string): boolean {
  return status === "PENDING_EXTRACTION";
}
