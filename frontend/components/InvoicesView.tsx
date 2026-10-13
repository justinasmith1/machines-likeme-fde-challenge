"use client";

import { useState } from "react";
import type { IntakeInvoice, InvoiceStatus, InvoiceSummary } from "@/lib/api/types";
import type { Session } from "@/lib/auth";
import { keys, useLoad, usePolling } from "@/lib/hooks";
import { isReviewer } from "@/lib/invoices/actions";
import { ALL_STATUSES, isPending, presentStatus } from "@/lib/invoices/status";
import { Empty, ErrorNotice, Loading } from "./Feedback";
import { IntakeInvoiceTable, ReviewerInvoiceTable } from "./InvoiceLists";
import { useApi } from "./Providers";
import { UploadForm } from "./UploadForm";

/** Upload plus the list. The list refreshes by itself only while something is processing. */
export function InvoicesView({ session }: { session: Session }) {
  const api = useApi();
  const reviewer = isReviewer(session.role);
  const [status, setStatus] = useState<InvoiceStatus | "">("");
  const list = useLoad(keys.invoiceList(status), () => api.listInvoices(status));

  const invoices = list.data ?? [];
  usePolling(
    list.reload,
    invoices.some((invoice) => isPending(invoice.status)),
  );

  return (
    <>
      <UploadForm onUploaded={() => void list.reload()} />

      <section className="panel" aria-labelledby="list-title">
        <h2 id="list-title">{reviewer ? "All invoices" : "Submitted documents"}</h2>
        {reviewer && (
          <div className="row">
            <label htmlFor="status-filter">Status</label>
            <select
              id="status-filter"
              value={status}
              onChange={(event) => setStatus(event.target.value as InvoiceStatus | "")}
            >
              <option value="">All</option>
              {ALL_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {presentStatus(value).label}
                </option>
              ))}
            </select>
          </div>
        )}
        {list.loading ? (
          <Loading what="Loading documents" />
        ) : list.error !== undefined && invoices.length === 0 ? (
          <ErrorNotice error={list.error} onRetry={() => void list.reload()} />
        ) : invoices.length === 0 ? (
          <Empty>{status ? "No documents with this status." : "No documents have been uploaded yet."}</Empty>
        ) : (
          <>
            {list.error !== undefined && <ErrorNotice error={list.error} onRetry={() => void list.reload()} />}
            {reviewer ? (
              <ReviewerInvoiceTable invoices={invoices as InvoiceSummary[]} caption="All invoices" from="invoices" />
            ) : (
              <IntakeInvoiceTable invoices={invoices as IntakeInvoice[]} />
            )}
          </>
        )}
      </section>
    </>
  );
}
