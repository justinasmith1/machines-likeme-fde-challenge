"use client";

import { AppShell } from "@/components/AppShell";
import { ReviewQueueView } from "@/components/ReviewQueueView";

export default function ReviewPage() {
  return (
    <AppShell title="Review queue" roles={["AP_ANALYST", "AP_MANAGER"]}>
      {() => <ReviewQueueView />}
    </AppShell>
  );
}
