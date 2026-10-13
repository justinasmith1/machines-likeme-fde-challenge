// What to show to whom. Two kinds of rules live here, and only here:
//
//  - Navigation and sections by role: which pages and panels a role gets.
//  - Decisions on an invoice: taken from what the backend says applies (`actions`), never
//    recomputed here. The frontend does not compare totals with the approval limit.
//
// Hiding something is a convenience, not security: the API enforces every permission, and
// the screens handle a 403 even when the button should not have been there.

import type { Actions, DecisionName, DecisionOption, Role } from "@/lib/api/types";

export interface NavItem {
  href: string;
  label: string;
}

const REVIEWERS: Role[] = ["AP_ANALYST", "AP_MANAGER"];

export function isReviewer(role: Role): boolean {
  return REVIEWERS.includes(role);
}

export function navigationFor(role: Role): NavItem[] {
  const items: NavItem[] = [{ href: "/invoices", label: "Invoices" }];
  if (isReviewer(role)) {
    items.unshift({ href: "/review", label: "Review queue" });
    items.push({ href: "/parameters", label: "Parameters" });
  }
  return items;
}

export function homeFor(role: Role): string {
  return isReviewer(role) ? "/review" : "/invoices";
}

export const canOpenInvoiceDetail = isReviewer;
export const canSeeParameters = isReviewer;
export const canSeeReviewQueue = isReviewer;

export function canEditParameters(role: Role): boolean {
  return role === "AP_MANAGER";
}

export function canSeeAuditTrail(role: Role): boolean {
  return role === "AP_MANAGER";
}

export const ROLE_LABELS: Record<Role, string> = {
  INTAKE: "Intake",
  AP_ANALYST: "AP Analyst",
  AP_MANAGER: "AP Manager",
};

interface DecisionText {
  label: string;
  /** What happens, in one sentence, shown next to the comment box. */
  effect: string;
  releasesPayment: boolean;
}

const DECISION_TEXT: Record<DecisionName, DecisionText> = {
  APPROVE: {
    label: "Approve for payment",
    effect: "The invoice will be approved for payment.",
    releasesPayment: true,
  },
  OVERRIDE: {
    label: "Override and approve",
    effect: "The automatic rejection will be overridden and the invoice approved for payment.",
    releasesPayment: true,
  },
  REJECT: {
    label: "Reject",
    effect: "The invoice will be rejected and will not be paid.",
    releasesPayment: false,
  },
  REQUEST_CREDIT_NOTE: {
    label: "Request credit note",
    effect: "The invoice will wait for a credit note from the supplier.",
    releasesPayment: false,
  },
};

export interface DecisionChoice extends DecisionText {
  decision: DecisionName;
  /** False when the backend says this user may not take it (for example, above the limit). */
  enabled: boolean;
  /** Text from the backend explaining a restriction, shown as is. */
  notice: string | null;
  /** Advice from the backend that does not block the decision, shown as is. */
  warning: string | null;
}

/** The decisions to offer, in the order and with the restrictions the backend gave. */
export function decisionChoices(actions: Actions | null | undefined): DecisionChoice[] {
  return (actions?.decisions ?? [])
    .filter((option: DecisionOption) => option.decision in DECISION_TEXT)
    .map((option) => ({
      ...DECISION_TEXT[option.decision],
      decision: option.decision,
      enabled: option.allowed,
      notice: option.notice,
      warning: option.warning ?? null,
    }));
}

export function canCorrect(actions: Actions | null | undefined): boolean {
  return actions?.can_correct === true;
}

export function canRetry(actions: Actions | null | undefined): boolean {
  return actions?.can_retry === true;
}

/** Plain names for the internal identifiers a reviewer sees. */
export const MAPPING_METHOD_LABELS: Record<string, string> = {
  PRINTED_PO_LINE: "PO line printed on the document",
  DESCRIPTION: "Same description as the PO line",
  ITEM_CODE: "Item code found in the description",
  UNMAPPED: "Not matched to a PO line",
};

export const SUPPLIER_METHOD_LABELS: Record<string, string> = {
  TAX_ID: "matched by tax ID",
  NAME: "matched by name",
  UNRESOLVED: "not found in the supplier master",
};

/** Where a rule comes from, as shown next to each reason. */
export const RULE_SOURCE_LABELS: Record<string, string> = {
  SPEC: "Specification",
  CONFIRMED: "Confirmed by client",
  ASSUMPTION: "Assumption",
};

/** One line per rule, for places that only have the code. */
export const RULE_DESCRIPTIONS: Record<string, string> = {
  R1: "The invoice references no purchase order",
  R2: "The referenced purchase order does not exist",
  R3: "The invoice supplier does not match the purchase order supplier",
  R4: "Duplicate of an invoice already processed",
  R5: "The invoice is dated before the purchase order",
  H1: "Quantity invoiced exceeds quantity received",
  H2: "Unit price differs from the purchase order beyond tolerance",
  H3: "A line cannot be matched to a purchase order line",
  A1: "The supplier is inactive",
  A2: "The purchase order is closed",
  A3: "Quantity invoiced exceeds quantity ordered",
  A4: "The invoice currency differs from the purchase order",
  A5: "Totals do not add up",
  A6: "A quantity or unit price is illegible",
  A7: "The purchase order number is illegible",
  A8: "Cumulative invoiced quantity exceeds received",
  A9: "The invoice number or date is missing",
  A10: "The invoice references more than one purchase order",
};

export const READ_STATUS_LABELS: Record<string, string> = {
  read: "Read",
  illegible: "Illegible",
  absent: "Not on the document",
};

/** The lists an invoice can be opened from. The detail page keeps it in `?from=`. */
export type InvoiceOrigin = "review" | "invoices";

const ORIGINS: Record<InvoiceOrigin, NavItem> = {
  review: { href: "/review", label: "Review queue" },
  invoices: { href: "/invoices", label: "Invoices" },
};

export function invoiceHref(invoiceId: number, from: InvoiceOrigin): string {
  return `/invoices/${invoiceId}?from=${from}`;
}

/** The list named by `?from=`, or null when it is missing or not one of ours. */
export function originFromQuery(from: string | null | undefined): NavItem | null {
  return from === "review" || from === "invoices" ? ORIGINS[from] : null;
}
