"use client";

// DEVELOPMENT LOGIN. This screen and lib/auth/dev are the only places that know how the
// development identity works. Phase 6 replaces both with Microsoft Entra ID sign-in.

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ErrorNotice } from "@/components/Feedback";
import { useApi } from "@/components/Providers";
import { isApiError } from "@/lib/api/errors";
import type { Role } from "@/lib/api/types";
import { auth } from "@/lib/auth";
import { DEV_ROLES, devSignIn } from "@/lib/auth/dev";
import { ROLE_LABELS, homeFor } from "@/lib/invoices/actions";

const ROLE_HINTS: Record<Role, string> = {
  INTAKE: "Uploads documents and follows their status.",
  AP_ANALYST: "Reviews held invoices, corrects data and decides within the approval limit.",
  AP_MANAGER: "Everything the analyst does, plus the audit trail and the parameters.",
};

export default function LoginPage() {
  const api = useApi();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("INTAKE");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    if (!username.trim()) {
      setProblem("Enter a user name.");
      return;
    }
    setProblem(null);
    setSending(true);
    try {
      devSignIn(username, role);
      const me = await api.me(); // confirms the API accepts this identity
      router.replace(homeFor(me.role));
    } catch (caught) {
      auth.signOut();
      if (isApiError(caught, "UNAUTHENTICATED")) {
        setProblem(
          "The API refused the development login. It accepts it only when the backend runs with APP_ENV=development.",
        );
      } else {
        setError(caught);
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="login">
      <h1>Contoso Invoice Matching</h1>
      <div className="notice notice-warning" role="note">
        <strong>Development login only.</strong> There is no password and identity is not verified: anyone can
        pick any role. It works only against a backend running in development mode, and it will be replaced by
        Microsoft Entra ID sign-in.
      </div>
      <form className="panel" onSubmit={submit}>
        <label htmlFor="login-user">User name</label>
        <input
          id="login-user"
          value={username}
          autoComplete="off"
          onChange={(event) => setUsername(event.target.value)}
        />
        <fieldset>
          <legend>Role</legend>
          {DEV_ROLES.map((value) => (
            <div key={value} className="choice">
              <label>
                <input type="radio" name="role" value={value} checked={role === value} onChange={() => setRole(value)} />{" "}
                {ROLE_LABELS[value]}
              </label>
              <p className="hint">{ROLE_HINTS[value]}</p>
            </div>
          ))}
        </fieldset>
        <button type="submit" disabled={sending}>
          {sending ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {problem && (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
    </main>
  );
}
