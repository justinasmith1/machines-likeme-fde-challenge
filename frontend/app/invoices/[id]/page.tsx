"use client";

import { useParams, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { InvoiceDetailView } from "@/components/invoice/InvoiceDetailView";
import { originFromQuery } from "@/lib/invoices/actions";

export default function InvoiceDetailPage() {
  const params = useParams<{ id: string }>();
  const invoiceId = Number(params?.id);
  // The list the person opened the invoice from. The navigation marks that one, or none
  // when the page was opened directly.
  const origin = originFromQuery(useSearchParams()?.get("from"));
  return (
    <AppShell title="Invoice" roles={["AP_ANALYST", "AP_MANAGER"]} activeHref={origin?.href ?? null}>
      {(session) =>
        Number.isInteger(invoiceId) && invoiceId > 0 ? (
          <InvoiceDetailView invoiceId={invoiceId} session={session} origin={origin} />
        ) : (
          <div className="notice notice-error" role="alert">
            <strong>Not found.</strong> This is not a valid invoice address.
          </div>
        )
      }
    </AppShell>
  );
}
