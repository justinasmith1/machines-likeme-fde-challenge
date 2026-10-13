// What each role is shown, the decisions offered from what the API lists, status names.

import { describe, expect, test } from "vitest";
import type { Actions, Role } from "@/lib/api/types";
import { extensionsLabel, formatAmount, formatPercent } from "@/lib/format";
import {
  canCorrect,
  canEditParameters,
  canOpenInvoiceDetail,
  canRetry,
  canSeeAuditTrail,
  canSeeParameters,
  canSeeReviewQueue,
  decisionChoices,
  homeFor,
  navigationFor,
} from "@/lib/invoices/actions";
import { ALL_STATUSES, isPending, presentStatus } from "@/lib/invoices/status";

const NOTICE =
  "The invoice total (7,559.47) is above the analyst approval limit (5,000.00). The AP Manager must decide.";

describe("what each role is shown", () => {
  const hrefs = (role: Role) => navigationFor(role).map((item) => item.href);

  test("intake gets the invoices page only", () => {
    expect(hrefs("INTAKE")).toEqual(["/invoices"]);
    expect(homeFor("INTAKE")).toBe("/invoices");
    expect(canOpenInvoiceDetail("INTAKE")).toBe(false);
    expect(canSeeReviewQueue("INTAKE")).toBe(false);
    expect(canSeeParameters("INTAKE")).toBe(false);
    expect(canSeeAuditTrail("INTAKE")).toBe(false);
    expect(canEditParameters("INTAKE")).toBe(false);
  });

  test("the analyst reviews and reads parameters, without audit trail or parameter editing", () => {
    expect(hrefs("AP_ANALYST")).toEqual(["/review", "/invoices", "/parameters"]);
    expect(homeFor("AP_ANALYST")).toBe("/review");
    expect(canOpenInvoiceDetail("AP_ANALYST")).toBe(true);
    expect(canSeeParameters("AP_ANALYST")).toBe(true);
    expect(canSeeAuditTrail("AP_ANALYST")).toBe(false);
    expect(canEditParameters("AP_ANALYST")).toBe(false);
  });

  test("the manager gets everything the analyst does, plus audit trail and parameter editing", () => {
    expect(hrefs("AP_MANAGER")).toEqual(hrefs("AP_ANALYST"));
    expect(canSeeAuditTrail("AP_MANAGER")).toBe(true);
    expect(canEditParameters("AP_MANAGER")).toBe(true);
  });
});

describe("decisions offered on an invoice", () => {
  const actions = (decisions: Actions["decisions"], extra: Partial<Actions> = {}): Actions => ({
    decisions,
    can_correct: true,
    can_retry: false,
    ...extra,
  });

  test("exactly the decisions the backend lists, in its order", () => {
    const choices = decisionChoices(
      actions([
        { decision: "APPROVE", allowed: true, required_role: "AP_ANALYST", notice: null },
        { decision: "REJECT", allowed: true, required_role: "AP_ANALYST", notice: null },
        { decision: "REQUEST_CREDIT_NOTE", allowed: true, required_role: "AP_ANALYST", notice: null },
      ]),
    );
    expect(choices.map((c) => c.decision)).toEqual(["APPROVE", "REJECT", "REQUEST_CREDIT_NOTE"]);
    expect(choices.map((c) => c.label)).toEqual(["Approve for payment", "Reject", "Request credit note"]);
    expect(choices.every((c) => c.enabled && c.notice === null)).toBe(true);
  });

  test("a rejected invoice offers an override, not an approval", () => {
    const choices = decisionChoices(
      actions([
        { decision: "OVERRIDE", allowed: true, required_role: "AP_ANALYST", notice: null },
        { decision: "REJECT", allowed: true, required_role: "AP_ANALYST", notice: null },
      ]),
    );
    expect(choices.map((c) => c.label)).toEqual(["Override and approve", "Reject"]);
    expect(choices[0].releasesPayment).toBe(true);
    expect(choices[1].releasesPayment).toBe(false);
  });

  test("a decision the backend does not allow this user is shown disabled, with the backend's notice", () => {
    const [approve, reject] = decisionChoices(
      actions([
        { decision: "APPROVE", allowed: false, required_role: "AP_MANAGER", notice: NOTICE },
        { decision: "REJECT", allowed: true, required_role: "AP_ANALYST", notice: null },
      ]),
    );
    expect(approve.enabled).toBe(false);
    expect(approve.notice).toBe(NOTICE); // shown as is; the frontend compares no amounts
    expect(reject.enabled).toBe(true);
  });

  test("the manager sees the same notice with the decision enabled", () => {
    const [approve] = decisionChoices(
      actions([{ decision: "APPROVE", allowed: true, required_role: "AP_MANAGER", notice: NOTICE }]),
    );
    expect(approve.enabled).toBe(true);
    expect(approve.notice).toBe(NOTICE);
  });

  test("nothing is offered when the backend lists nothing, or sends no actions", () => {
    expect(decisionChoices(actions([]))).toEqual([]);
    expect(decisionChoices(null)).toEqual([]);
    expect(decisionChoices(undefined)).toEqual([]);
  });

  test("a decision this version does not know is ignored rather than shown without a label", () => {
    const choices = decisionChoices(
      actions([{ decision: "ESCALATE" as never, allowed: true, required_role: "AP_MANAGER", notice: null }]),
    );
    expect(choices).toEqual([]);
  });

  test("correct and retry follow the backend's flags", () => {
    expect(canCorrect(actions([]))).toBe(true);
    expect(canCorrect(actions([], { can_correct: false }))).toBe(false);
    expect(canRetry(actions([], { can_retry: true }))).toBe(true);
    expect(canRetry(actions([]))).toBe(false);
    expect(canCorrect(null)).toBe(false);
    expect(canRetry(undefined)).toBe(false);
  });
});

describe("status presentation", () => {
  test("every status has a readable name, a symbol and a description", () => {
    for (const status of ALL_STATUSES) {
      const shown = presentStatus(status);
      expect(shown.label).not.toBe(status);
      expect(shown.label).not.toMatch(/_/);
      expect(shown.symbol.length).toBeGreaterThan(0);
      expect(shown.description.length).toBeGreaterThan(10);
    }
    expect(ALL_STATUSES).toHaveLength(10);
  });

  test("statuses that share a colour differ in name", () => {
    const success = ALL_STATUSES.filter((s) => presentStatus(s).tone === "success").map((s) => presentStatus(s).label);
    expect(new Set(success).size).toBe(success.length);
    expect(presentStatus("AUTO_PAY").label).toBe("Auto-Pay");
    expect(presentStatus("APPROVED_FOR_PAYMENT").label).toBe("Approved for payment");
    expect(presentStatus("HELD_FOR_REVIEW").tone).toBe("warning");
    expect(presentStatus("REJECTED_BY_REVIEWER").tone).toBe("danger");
  });

  test("an unknown status is shown as is instead of breaking", () => {
    expect(presentStatus("SOMETHING_NEW").label).toBe("SOMETHING_NEW");
  });

  test("only a document being processed keeps the page polling", () => {
    expect(ALL_STATUSES.filter(isPending)).toEqual(["PENDING_EXTRACTION"]);
  });
});

describe("formatting", () => {
  test("amounts are grouped for reading without changing their digits", () => {
    expect(formatAmount("7559.47")).toBe("7,559.47");
    expect(formatAmount("1234567.00", "USD")).toBe("1,234,567.00 USD");
    expect(formatAmount("0.405")).toBe("0.405");
    expect(formatAmount("121.00")).toBe("121.00");
    expect(formatAmount(null)).toBe("–");
    expect(formatAmount("n/a")).toBe("n/a");
  });

  test("the tolerance is shown as a percentage", () => {
    expect(formatPercent("0.02")).toBe("2%");
    expect(formatPercent("0.025")).toBe("2.5%");
    expect(formatPercent("0.5")).toBe("50%");
    expect(formatPercent("1")).toBe("100%");
    expect(formatPercent("0.005")).toBe("0.5%");
  });

  test("accepted file types are listed once each", () => {
    expect(extensionsLabel([".pdf", ".png", ".jpg", ".jpeg"])).toBe("PDF, PNG, JPG");
  });
});
