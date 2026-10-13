// @vitest-environment node
//
// Contract check of the frontend's API client against a RUNNING backend. Skipped unless
// LIVE_API_URL is set, so `npm test` never needs a backend.
//
//   LIVE_API_URL=http://127.0.0.1:8000/api LIVE_INVOICE_DIR=../data/test-invoices npm test -- live-api
//
// The backend must run with APP_ENV=development, reference data loaded, and a database with
// no invoices yet. The two documents must be in the extraction cache, or the backend will
// call the model. It uses the same client module the screens use, once per role.

import { readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { describe, expect, test } from "vitest";
import { createApiClient, type ApiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { isReviewerDetail, type IntakeInvoice, type ReviewerInvoice, type Role } from "@/lib/api/types";

const baseUrl = process.env.LIVE_API_URL ?? "";
const invoiceDir = process.env.LIVE_INVOICE_DIR ?? "../data/test-invoices";

function clientFor(username: string, role: Role | null): ApiClient {
  return createApiClient({
    baseUrl,
    authHeaders: (): Record<string, string> => (role ? { "X-Dev-User": username, "X-Dev-Role": role } : {}),
  });
}

const intake = clientFor("ines.intake", "INTAKE");
const analyst = clientFor("ana.analyst", "AP_ANALYST");
const manager = clientFor("max.manager", "AP_MANAGER");

function document(name: string, type: string): File {
  return new File([readFileSync(join(invoiceDir, name))], basename(name), { type });
}

async function waitUntilProcessed(id: number): Promise<IntakeInvoice> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const found = ((await intake.listInvoices()) as IntakeInvoice[]).find((invoice) => invoice.id === id)!;
    if (found.status !== "PENDING_EXTRACTION") return found;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`invoice ${id} is still pending`);
}

async function reviewerDetail(client: ApiClient, id: number): Promise<ReviewerInvoice> {
  const detail = await client.getInvoice(id);
  if (!isReviewerDetail(detail)) throw new Error("expected the reviewer view");
  return detail;
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  const result = await promise.then(
    () => null,
    (error: unknown) => error,
  );
  expect(result).toBeInstanceOf(ApiError);
  return result as ApiError;
}

describe.skipIf(!baseUrl)("frontend API client against a running backend", () => {
  let first = 0;
  let second = 0;

  test("identity and upload limits come from the API", async () => {
    expect(await intake.me()).toEqual({
      username: "ines.intake",
      role: "INTAKE",
      upload: { max_mb: 10, extensions: [".pdf", ".png", ".jpg", ".jpeg"] },
    });
    expect((await failure(clientFor("nobody", null).me())).code).toBe("UNAUTHENTICATED");
  });

  test("intake uploads a document and sees it processed, with plain reasons only", async () => {
    const receipt = await intake.uploadInvoice(document("INV-05.pdf", "application/pdf"));
    expect(receipt.status).toBe("PENDING_EXTRACTION");
    first = receipt.id;

    const processed = await waitUntilProcessed(first);
    expect(Object.keys(processed).sort()).toEqual(["filename", "id", "reasons", "status", "uploaded_at"]);
    expect(processed.status).toBe("HELD_FOR_REVIEW");
    expect(processed.reasons).toHaveLength(1);
    expect(processed.reasons[0]).toMatch(/unit price .* differs from PO price/);

    const own = await intake.getInvoice(first);
    expect(isReviewerDetail(own)).toBe(false);
    expect(JSON.stringify(own)).not.toMatch(/"code"|parameters|decided_by|DL-/);
  }, 30_000);

  test("intake is refused everything that is not for intake", async () => {
    expect((await failure(intake.reviewQueue())).code).toBe("PERMISSION_DENIED");
    expect((await failure(intake.getParameters())).code).toBe("PERMISSION_DENIED");
    expect((await failure(intake.getAudit(first))).code).toBe("PERMISSION_DENIED");
    expect((await failure(intake.decide(first, "APPROVE", "Looks fine."))).code).toBe("PERMISSION_DENIED");
    expect((await failure(intake.correctExtraction(first, { header: { currency: "EUR" } }, "x"))).status).toBe(403);
    expect((await intake.getDocument(first)).size).toBeGreaterThan(0); // the document itself is allowed
  });

  test("the analyst sees the queue and the full detail, with the actions computed by the API", async () => {
    expect((await analyst.reviewQueue()).map((invoice) => invoice.id)).toContain(first);

    const detail = await reviewerDetail(analyst, first);
    expect(detail.invoice.status).toBe("HELD_FOR_REVIEW");
    expect(detail.actions.decisions.map((d) => d.decision)).toEqual(["APPROVE", "REJECT", "REQUEST_CREDIT_NOTE"]);
    expect(detail.actions.decisions.every((d) => d.allowed && d.notice === null)).toBe(true);
    expect(detail.actions.can_correct).toBe(true);
    expect(detail.evaluation!.reasons[0]).toMatchObject({ code: "H2", source: "SPEC" });
    expect(detail.extraction!.header!.invoice_number.status).toBe("read");
    expect(detail.extraction!.lines![0].po_line_mapping!.method).toBe("DESCRIPTION");
    expect(detail.extraction_versions.map((v) => v.source)).toEqual(["MODEL"]);
    // Supplier legal name from the master, origin of each field, and the evaluation history.
    expect(detail.invoice.supplier_name).toBeTruthy();
    expect(detail.invoice.supplier_name).not.toBe(detail.invoice.supplier_id);
    expect(detail.extraction!.header!.invoice_number.origin).toEqual({ source: "MODEL" });
    expect(detail.evaluation_history!.map((e) => [e.outcome, e.extraction_version])).toEqual([["HELD_FOR_REVIEW", 1]]);
    expect(detail.actions.decisions.every((d) => d.warning === null)).toBe(true); // a PO line is matched
    expect((await analyst.reviewQueue()).find((invoice) => invoice.id === first)!.supplier_name).toBe(
      detail.invoice.supplier_name,
    );

    const file = await analyst.getDocument(first);
    expect(file.type).toBe("application/pdf");
    expect(file.size).toBeGreaterThan(1000);

    expect((await analyst.getParameters()).analyst_approval_limit).toBe("5000.00");
    expect((await failure(analyst.getAudit(first))).code).toBe("PERMISSION_DENIED");
    expect((await failure(analyst.updateParameters({ rounding_tolerance: "0.02" }, "x"))).code).toBe("PERMISSION_DENIED");
    expect((await failure(analyst.parameterHistory())).code).toBe("PERMISSION_DENIED");
  });

  test("a decision needs a comment; an invalid one is a clear error", async () => {
    const noComment = await failure(analyst.decide(first, "REJECT", "  "));
    expect(noComment.code).toBe("VALIDATION_ERROR");
    expect(noComment.message).toContain("comment is required");
    const wrongKind = await failure(analyst.decide(first, "OVERRIDE", "Override."));
    expect(wrongKind.code).toBe("INVALID_STATE");
  });

  test("a correction leaves the invoice held; approving is a separate step (DL-46)", async () => {
    const corrected = await analyst.correctExtraction(first, { header: { due_date: "2026-09-30" } }, "Due date misread.");
    expect(corrected.invoice.status).toBe("HELD_FOR_REVIEW");
    expect(corrected.invoice.status_reason).toBe(
      "Extracted data was corrected by ana.analyst; a human decision is required before payment.",
    );
    expect(corrected.extraction_versions.map((v) => v.source)).toEqual(["MODEL", "HUMAN_CORRECTION"]);
    expect(corrected.extraction!.header!.due_date).toBe("2026-09-30");
    // The corrected field says who changed it and what it was; the earlier evaluation is kept.
    expect(corrected.extraction!.corrected_fields!.due_date).toMatchObject({
      source: "HUMAN_CORRECTION",
      corrected_by: "ana.analyst",
      version: 2,
    });
    expect(corrected.extraction!.header!.invoice_number.origin).toEqual({ source: "MODEL" });
    expect(corrected.evaluation_history!.map((e) => e.extraction_version)).toEqual([1, 2]);
    expect(corrected.evaluation_history![0].reasons[0].code).toBe("H2");
    expect(corrected.actions.decisions.map((d) => d.decision)).toContain("APPROVE");

    const seenByIntake = (await intake.getInvoice(first)) as IntakeInvoice;
    expect(seenByIntake.reasons).toEqual(["The extracted data was corrected by a reviewer; a decision is pending."]);

    const approved = await analyst.decide(first, "APPROVE", "Price increase agreed with purchasing.");
    expect(approved.invoice.status).toBe("APPROVED_FOR_PAYMENT");
    expect(approved.decisions).toHaveLength(1);
    expect(approved.decisions[0]).toMatchObject({ decision: "APPROVE", decided_by: "ana.analyst", superseded: false });
    expect(approved.actions.decisions.map((d) => d.decision)).toEqual(["REJECT", "REQUEST_CREDIT_NOTE"]);
    expect((await analyst.reviewQueue()).map((invoice) => invoice.id)).not.toContain(first);
  });

  test("above the limit the API tells the analyst so, refuses, and accepts the manager", async () => {
    second = (await intake.uploadInvoice(document("INV-06.png", "image/png"))).id;
    await waitUntilProcessed(second);

    const forAnalyst = (await reviewerDetail(analyst, second)).actions.decisions[0];
    expect(forAnalyst).toMatchObject({ decision: "APPROVE", allowed: false, required_role: "AP_MANAGER" });
    expect(forAnalyst.notice).toMatch(/above the analyst approval limit \(5,000\.00\)/);

    const refused = await failure(analyst.decide(second, "APPROVE", "Approved."));
    expect(refused.status).toBe(403);
    expect(refused.message).toBe(forAnalyst.notice); // what the screen warned is what the API enforces

    const forManager = (await reviewerDetail(manager, second)).actions.decisions[0];
    expect(forManager).toMatchObject({ decision: "APPROVE", allowed: true, required_role: "AP_MANAGER" });

    const approved = await manager.decide(second, "APPROVE", "Cable price confirmed with the supplier.");
    expect(approved.invoice.status).toBe("APPROVED_FOR_PAYMENT");
    expect(approved.decisions[0].required_role).toBe("AP_MANAGER");
    expect((await analyst.getDocument(second)).type).toBe("image/png");
  }, 30_000);

  test("the manager sees the audit trail and manages parameters", async () => {
    const events = await manager.getAudit(first);
    expect(events.map((event) => event.event_type)).toEqual([
      "INVOICE_SUBMITTED",
      "EXTRACTION_SUCCEEDED",
      "INVOICE_EVALUATED",
      "DECISION_DENIED", // the intake user's attempt, refused and recorded
      "EXTRACTION_CORRECTED",
      "INVOICE_EVALUATED",
      "HELD_AFTER_CORRECTION",
      "HUMAN_DECISION_RECORDED",
    ]);
    expect((await manager.getAudit(second)).map((event) => event.event_type)).toContain("DECISION_DENIED");
    // The audit trail carries what a correction changed and the message of each rule.
    const correction = events.find((event) => event.event_type === "EXTRACTION_CORRECTED")!;
    expect(correction.details.changes).toEqual([
      expect.objectContaining({ scope: "header", field: "due_date", after: { value: "2026-09-30", status: null } }),
    ]);
    const evaluated = events.find((event) => event.event_type === "INVOICE_EVALUATED")!;
    expect((evaluated.details.reasons as Array<{ code: string; message: string }>)[0]).toMatchObject({ code: "H2" });
    // A6, A7 and A9 are fixed rules: they are not among the switches.
    const switches = (await manager.getParameters()).enabled_assumption_rules;
    for (const fixed of ["A6", "A7", "A9"]) expect(switches).not.toContain(fixed);

    const noComment = await failure(manager.updateParameters({ price_tolerance_pct: "0.05" }, ""));
    expect(noComment.code).toBe("VALIDATION_ERROR");
    const updated = await manager.updateParameters({ price_tolerance_pct: "0.05" }, "Agreed with the CFO.");
    expect(updated.version).toBe(2);
    expect((await manager.parameterHistory()).map((v) => [v.version, v.comment])).toEqual([
      [2, "Agreed with the CFO."],
      [1, "Initial values from configuration."],
    ]);
    // An earlier evaluation keeps the parameters it was made with.
    expect((await reviewerDetail(manager, second)).evaluation!.parameters_snapshot.version).toBe(1);
  });

  test("reviewers see every invoice, and the status filter works", async () => {
    const all = await analyst.listInvoices();
    expect(all.map((invoice) => invoice.id).sort()).toEqual([first, second].sort());
    expect(await analyst.listInvoices("HELD_FOR_REVIEW")).toEqual([]);
    expect((await failure(analyst.getInvoice(9999))).code).toBe("NOT_FOUND");
  });

  test("uploads the API refuses come back with their own code", async () => {
    const wrongType = await failure(intake.uploadInvoice(new File(["x"], "invoice.docx")));
    expect(wrongType.code).toBe("UNSUPPORTED_DOCUMENT_TYPE");
    const tooBig = await failure(
      intake.uploadInvoice(new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.pdf", { type: "application/pdf" })),
    );
    expect(tooBig.code).toBe("FILE_TOO_LARGE");
    expect(tooBig.status).toBe(413);
  });
});
