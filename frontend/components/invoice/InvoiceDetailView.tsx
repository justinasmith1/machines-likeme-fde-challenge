"use client";

import Link from "next/link";
import { useState } from "react";
import { isReviewerDetail, type ReviewerInvoice } from "@/lib/api/types";
import type { Session } from "@/lib/auth";
import { formatAmount, formatDateTime, supplierLabel } from "@/lib/format";
import { invoiceChanged, keys, useLoad, usePolling } from "@/lib/hooks";
import { canCorrect, canRetry, canSeeAuditTrail, type NavItem } from "@/lib/invoices/actions";
import { isPending, presentStatus } from "@/lib/invoices/status";
import { ErrorNotice, Loading, Notice, StatusBadge } from "../Feedback";
import { useApi } from "../Providers";
import { DecisionPanel } from "./DecisionPanel";
import { DocumentViewer } from "./DocumentViewer";
import { EvaluationPanel } from "./EvaluationPanel";
import { ExtractionPanel, LinesPanel } from "./ExtractionPanel";
import { AuditTimeline, HistoryCard } from "./HistoryPanels";

const CORRECTED_PREFIX = "Extracted data was corrected";

const REVIEW_QUEUE: NavItem = { href: "/review", label: "Review queue" };

export function InvoiceDetailView({
  invoiceId,
  session,
  origin,
}: {
  invoiceId: number;
  session: Session;
  /** The list the invoice was opened from; the link back goes there. */
  origin?: NavItem | null;
}) {
  const back = origin ?? REVIEW_QUEUE;
  const api = useApi();
  const detail = useLoad(keys.invoice(invoiceId), () => api.getInvoice(invoiceId));
  const [justCorrected, setJustCorrected] = useState(false);
  const [retryError, setRetryError] = useState<unknown>(undefined);
  const [retrying, setRetrying] = useState(false);

  const data = detail.data;
  usePolling(detail.reload, data !== undefined && isPending(isReviewerDetail(data) ? data.invoice.status : data.status));

  if (detail.loading) return <Loading what="Loading the invoice" />;
  if (detail.error !== undefined && !data) {
    return <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />;
  }
  if (!data) return null;

  if (!isReviewerDetail(data)) {
    // The API gave the intake view: file, date, status and plain reasons only.
    return (
      <section className="panel">
        <h2>{data.filename}</h2>
        <p>
          <StatusBadge status={data.status} /> <span className="muted">uploaded {formatDateTime(data.uploaded_at)}</span>
        </p>
        {data.reasons.length > 0 ? (
          <ul>
            {data.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p>{presentStatus(data.status).description}</p>
        )}
      </section>
    );
  }

  const { invoice, actions, extraction, evaluation, decisions, extraction_versions: versions } = data;
  // The action's answer is the invoice as it is now; lists and the audit trail are out of date.
  const update = (next: ReviewerInvoice) => invoiceChanged(invoiceId, next);

  async function retry() {
    setRetryError(undefined);
    setRetrying(true);
    try {
      await api.retryExtraction(invoiceId);
      invoiceChanged(invoiceId);
      await detail.reload();
    } catch (caught) {
      setRetryError(caught);
    } finally {
      setRetrying(false);
    }
  }

  const heldAfterCorrection =
    invoice.status === "HELD_FOR_REVIEW" && (invoice.status_reason?.startsWith(CORRECTED_PREFIX) ?? false);

  return (
    <>
      <p>
        <Link href={back.href}>← {back.label}</Link>
      </p>
      <section className="panel summary" aria-label="Invoice summary">
        <div>
          <span className="label">Document</span>
          <strong className="block">{invoice.filename}</strong>
          <span className="muted block">
            uploaded {formatDateTime(invoice.uploaded_at)} by {invoice.uploaded_by}
          </span>
        </div>
        <div>
          <span className="label">Status</span>
          <span className="block">
            <StatusBadge status={invoice.status} />
          </span>
          <span className="muted block">{presentStatus(invoice.status).description}</span>
        </div>
        <div>
          <span className="label">Supplier</span>
          <span className="block">{supplierLabel(invoice.supplier_name, invoice.supplier_id)}</span>
          <span className="muted block">Invoice {invoice.invoice_number ?? "–"}</span>
        </div>
        <div>
          <span className="label">Total</span>
          <strong className="block amount total">{formatAmount(invoice.total, invoice.currency)}</strong>
        </div>
      </section>

      {justCorrected && (
        <Notice tone="success">
          <strong>Correction saved.</strong> The invoice was evaluated again with the corrected data. Saving a
          correction does not approve payment: it stays held until someone records a decision below.
        </Notice>
      )}
      {invoice.status_reason && (
        <Notice tone={heldAfterCorrection ? "warning" : "info"}>
          {invoice.status_reason}
          {heldAfterCorrection && evaluation
            ? ` The rules engine's result for the corrected data is "${presentStatus(evaluation.outcome).label}"; approving is a separate action.`
            : ""}
        </Notice>
      )}
      {detail.error !== undefined && <ErrorNotice error={detail.error} onRetry={() => void detail.reload()} />}

      {canRetry(actions) && (
        <section className="panel" aria-labelledby="retry-title">
          <h2 id="retry-title">Retry</h2>
          <p className="hint">The document could not be processed. Retrying reads it again from the stored file.</p>
          <button type="button" onClick={() => void retry()} disabled={retrying}>
            {retrying ? "Retrying…" : "Retry extraction"}
          </button>
          {retryError !== undefined && <ErrorNotice error={retryError} />}
        </section>
      )}

      {/* Wide screens: the document stays in view while the extracted data and the evaluation scroll. */}
      <div className="split">
        <div className="split-document">
          <h2 className="sr-only">Original document</h2>
          <DocumentViewer invoiceId={invoice.id} filename={invoice.filename} />
        </div>
        <div>
          {extraction ? (
            <ExtractionPanel
              extraction={extraction}
              correction={
                canCorrect(actions)
                  ? {
                      invoiceId: invoice.id,
                      onCorrected: (next) => {
                        setJustCorrected(true);
                        update(next);
                      },
                    }
                  : undefined
              }
            />
          ) : (
            <section className="panel">
              <h2>Extracted data</h2>
              <p className="state state-empty">
                {isPending(invoice.status)
                  ? "The document is being read. This page updates by itself."
                  : "Nothing was extracted from this document."}
              </p>
            </section>
          )}
          <EvaluationPanel evaluation={evaluation} heldAfterCorrection={heldAfterCorrection} />
        </div>
      </div>

      {/* Across the whole page, in the order a reviewer reads: the lines, then the decision. */}
      {extraction?.header && <LinesPanel extraction={extraction} />}
      <DecisionPanel
        invoiceId={invoice.id}
        actions={actions}
        onDecided={(next) => {
          setJustCorrected(false);
          update(next);
        }}
      />
      <HistoryCard
        decisions={decisions}
        evaluations={data.evaluation_history ?? (evaluation ? [evaluation] : [])}
        versions={versions}
      />
      {canSeeAuditTrail(session.role) && (
        <AuditTimeline invoiceId={invoice.id} refreshKey={`${invoice.status}-${decisions.length}-${versions.length}`} />
      )}
    </>
  );
}
