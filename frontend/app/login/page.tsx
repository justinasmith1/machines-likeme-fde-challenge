"use client";

// Two screens, one per identity provider (lib/auth):
//  - Development: a login with no password. This screen and lib/auth/dev are the only
//    places that know how the development identity works.
//  - Entra: nobody signs in here. Azure sends visitors to Microsoft's sign-in page before
//    they reach the app; this screen is only for someone who signed in but has no role in
//    the application, or whose session ended.

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ErrorNotice, Loading } from "@/components/Feedback";
import { useApi } from "@/components/Providers";
import { isApiError } from "@/lib/api/errors";
import type { Role } from "@/lib/api/types";
import { auth, useSession } from "@/lib/auth";
import { DEV_ROLES, devSignIn } from "@/lib/auth/dev";
import { SIGN_IN_URL, SIGN_OUT_URL } from "@/lib/auth/entra";
import { ROLE_LABELS, homeFor } from "@/lib/invoices/actions";

const ROLE_HINTS: Record<Role, string> = {
  INTAKE: "Uploads documents and follows their status.",
  AP_ANALYST: "Reviews held invoices, corrects data and decides within the approval limit.",
  AP_MANAGER: "Everything the analyst does, plus the audit trail and the parameters.",
};

export default function LoginPage() {
  return auth.mode === "entra" ? <EntraSignIn /> : <DevelopmentLogin />;
}

function EntraSignIn() {
  const session = useSession();
  const router = useRouter();
  useEffect(() => {
    if (session) router.replace(homeFor(session.role));
  }, [session, router]);

  if (session === undefined || session) return <Loading what="Checking your session" />;
  return (
    <main className="login">
      <h1>Contoso Invoice Matching</h1>
      <div className="notice notice-warning" role="alert">
        <strong>You do not have access.</strong> Either you are not signed in, or your account has not been
        given a role in this application (Intake, AP Analyst or AP Manager). Roles are assigned by the
        administrator of the directory.
      </div>
      <p>
        <a href={SIGN_IN_URL}>Sign in with Microsoft</a> · <a href={SIGN_OUT_URL}>Sign out and use another account</a>
      </p>
    </main>
  );
}

function DevelopmentLogin() {
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
