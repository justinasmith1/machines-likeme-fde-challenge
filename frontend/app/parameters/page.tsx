"use client";

import { AppShell } from "@/components/AppShell";
import { ParametersView } from "@/components/ParametersView";

export default function ParametersPage() {
  return (
    <AppShell title="Parameters" roles={["AP_ANALYST", "AP_MANAGER"]}>
      {(session) => <ParametersView session={session} />}
    </AppShell>
  );
}
