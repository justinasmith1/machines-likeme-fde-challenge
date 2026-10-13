"use client";

import { AppShell } from "@/components/AppShell";
import { InvoicesView } from "@/components/InvoicesView";

export default function InvoicesPage() {
  return <AppShell title="Invoices">{(session) => <InvoicesView session={session} />}</AppShell>;
}
