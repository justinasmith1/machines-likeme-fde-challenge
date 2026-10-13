"use client";

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { describeError, isApiError } from "@/lib/api/errors";
import { auth } from "@/lib/auth";
import { presentStatus } from "@/lib/invoices/status";

export function StatusBadge({ status }: { status: string }) {
  const { label, tone, symbol, description } = presentStatus(status);
  return (
    <span className={`badge badge-${tone}`} title={description}>
      <span aria-hidden="true">{symbol}</span> {label}
    </span>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="state" role="status" aria-live="polite">
      {what}…
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state state-empty">{children}</p>;
}

interface ErrorNoticeProps {
  error: unknown;
  /** Offered for failures that may go away, such as a connection problem. */
  onRetry?: () => void;
}

/** Shows any failure with the text chosen for its code. Never a raw error or a stack trace. */
export function ErrorNotice({ error, onRetry }: ErrorNoticeProps) {
  const router = useRouter();
  const { code, title, message } = describeError(error);
  const unauthenticated = isApiError(error, "UNAUTHENTICATED");
  return (
    <div className="notice notice-error" role="alert" data-error-code={code}>
      <strong>{title}.</strong> {message}
      {unauthenticated && (
        <button
          type="button"
          className="link"
          onClick={() => {
            auth.signOut();
            router.replace("/login");
          }}
        >
          Sign in again
        </button>
      )}
      {!unauthenticated && onRetry && (
        <button type="button" className="link" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "success" | "warning"; children: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role="status">
      {children}
    </div>
  );
}
