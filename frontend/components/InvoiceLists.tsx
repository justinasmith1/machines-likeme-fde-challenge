"use client";

import Link from "next/link";
import { invoiceHref, type InvoiceOrigin } from "@/lib/invoices/actions";
import type { IntakeInvoice, InvoiceSummary } from "@/lib/api/types";
import { formatAmount, formatDateTime, supplierLabel } from "@/lib/format";
import { presentStatus } from "@/lib/invoices/status";
import { StatusBadge } from "./Feedback";

/** Exactly what the intake view of the API returns: file, date, status, plain reasons. */
export function IntakeInvoiceTable({ invoices }: { invoices: IntakeInvoice[] }) {
  return (
    <table>
      <caption className="sr-only">Submitted documents</caption>
      <thead>
        <tr>
          <th scope="col">File</th>
          <th scope="col">Uploaded</th>
          <th scope="col">Status</th>
          <th scope="col">What it means</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((invoice) => (
          <tr key={invoice.id}>
            <td>{invoice.filename}</td>
            <td>{formatDateTime(invoice.uploaded_at)}</td>
            <td>
              <StatusBadge status={invoice.status} />
            </td>
            <td>
              {invoice.reasons.length > 0 ? (
                <ul className="plain">
                  {invoice.reasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              ) : (
                <span className="muted">{presentStatus(invoice.status).description}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReviewerInvoiceTable({
  invoices,
  caption,
  from,
}: {
  invoices: InvoiceSummary[];
  caption: string;
  /** The list this table is shown in, so the invoice page knows where the person came from. */
  from: InvoiceOrigin;
}) {
  return (
    <table>
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">File</th>
          <th scope="col">Uploaded</th>
          <th scope="col">Supplier</th>
          <th scope="col">Invoice no.</th>
          <th scope="col" className="num">
            Total
          </th>
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((invoice) => (
          <tr key={invoice.id}>
            <td>
              <Link href={invoiceHref(invoice.id, from)}>{invoice.filename}</Link>
            </td>
            <td>
              {formatDateTime(invoice.uploaded_at)}
              <br />
              <span className="muted">by {invoice.uploaded_by}</span>
            </td>
            <td>{supplierLabel(invoice.supplier_name, invoice.supplier_id)}</td>
            <td className="code">{invoice.invoice_number ?? "–"}</td>
            <td className="num">{formatAmount(invoice.total, invoice.currency)}</td>
            <td>
              <StatusBadge status={invoice.status} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
