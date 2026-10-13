"use client";

import { keys, useLoad } from "@/lib/hooks";
import { Empty, ErrorNotice, Loading } from "./Feedback";
import { ReviewerInvoiceTable } from "./InvoiceLists";
import { useApi } from "./Providers";

/** Invoices held for review with no decision in force, oldest first (the API's order). */
export function ReviewQueueView() {
  const api = useApi();
  const queue = useLoad(keys.reviewQueue, () => api.reviewQueue());

  if (queue.loading) return <Loading what="Loading the review queue" />;
  if (queue.error !== undefined && !queue.data) {
    return <ErrorNotice error={queue.error} onRetry={() => void queue.reload()} />;
  }
  const invoices = queue.data ?? [];
  return (
    <section className="panel" aria-labelledby="queue-title">
      <h2 id="queue-title">Waiting for a decision</h2>
      <p className="hint">Oldest first. Open an invoice to see the document, the reasons and to decide.</p>
      {invoices.length === 0 ? (
        <Empty>Nothing is waiting for review.</Empty>
      ) : (
        <ReviewerInvoiceTable invoices={invoices} caption="Invoices waiting for a decision" from="review" />
      )}
      <button type="button" className="secondary" onClick={() => void queue.reload()}>
        Refresh
      </button>
    </section>
  );
}
