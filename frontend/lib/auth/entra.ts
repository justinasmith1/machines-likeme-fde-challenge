// Microsoft Entra ID sign-in, done by Azure Container Apps authentication ("Easy Auth")
// in front of this app. Nobody signs in on a page of ours: an unauthenticated visitor is
// sent to Microsoft's sign-in page before any request reaches this code, and the session
// lives in a cookie the browser sends by itself (so authHeaders has nothing to add).
//
// Who is signed in is read from /.auth/me, the endpoint Easy Auth serves on the same
// origin. The role is the app role assigned in Entra ID; with several, the one with the
// most privilege applies (DL-83). This is only what the screens show and offer: the
// backend works out the role again from the identity Azure injects, and enforces it.
//
// Nothing outside lib/auth may import this file, except the login screen (for the
// sign-in and sign-out addresses).

import type { Role } from "@/lib/api/types";
import type { AuthProvider, Session } from "./types";

export const AUTH_ME_URL = "/.auth/me";
/** The backend's reading of the same identity. Used when /.auth/me is not served. */
export const API_ME_URL = "/api/me";
export const SIGN_IN_URL = "/.auth/login/aad?post_login_redirect_uri=/";
export const SIGN_OUT_URL = "/.auth/logout?post_logout_redirect_uri=/";

/** Most privilege first. */
export const ROLE_PRIORITY: Role[] = ["AP_MANAGER", "AP_ANALYST", "INTAKE"];

const ROLE_CLAIMS = new Set(["roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"]);
const USERNAME_CLAIMS = [
  "preferred_username",
  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
  "email",
  "name",
];

const text = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

/** The role with the most privilege among the values, or null when none is ours. */
export function roleFrom(values: unknown[]): Role | null {
  const known = new Set(values.map((value) => (typeof value === "string" ? value.trim().toUpperCase() : "")));
  return ROLE_PRIORITY.find((role) => known.has(role)) ?? null;
}

/**
 * The session in an answer of /.auth/me: a list with one entry per signed-in identity,
 * each with `user_id` and `user_claims` ({typ, val} pairs). Null when it names nobody or
 * carries no role of this application.
 */
export function sessionFromAuthMe(payload: unknown): Session | null {
  const entry = Array.isArray(payload) ? payload[0] : payload;
  if (!entry || typeof entry !== "object") return null;
  const record = entry as Record<string, unknown>;
  const claims = (Array.isArray(record.user_claims) ? record.user_claims : [])
    .filter((claim): claim is { typ: string; val: string } => {
      const pair = claim as { typ?: unknown; val?: unknown } | null;
      return typeof pair?.typ === "string" && typeof pair?.val === "string";
    });

  const roleValues: unknown[] = claims.filter((claim) => ROLE_CLAIMS.has(claim.typ)).map((claim) => claim.val);
  if (Array.isArray(record.roles)) roleValues.push(...record.roles);
  const role = roleFrom(roleValues);

  const fromClaims = USERNAME_CLAIMS.map((typ) => text(claims.find((claim) => claim.typ === typ)?.val)).find(Boolean);
  const username = fromClaims ?? text(record.user_id);
  return username && role ? { username, role } : null;
}

/** The session in the backend's /api/me answer: {username, role}. */
function sessionFromApiMe(payload: unknown): Session | null {
  const record = (payload ?? {}) as Record<string, unknown>;
  const username = text(record.username);
  const role = roleFrom([record.role]);
  return username && role ? { username, role } : null;
}

export interface EntraOptions {
  fetchImpl?: typeof fetch;
  /** Where the browser is sent to sign out. Tests replace it. */
  navigate?: (url: string) => void;
}

/**
 * Asks who is signed in. 401 means nobody. When /.auth/me is not served at all (some
 * Easy Auth configurations do not expose it), the backend is asked instead: it reads
 * the same identity from the header Azure injects.
 */
export async function loadSession(fetchImpl: typeof fetch): Promise<Session | null> {
  const read = async (url: string): Promise<{ status: number; body: unknown }> => {
    const response = await fetchImpl(url, { credentials: "same-origin", headers: { accept: "application/json" } });
    let body: unknown = null;
    if (response.ok) body = await response.json().catch(() => null);
    return { status: response.status, body };
  };

  try {
    const me = await read(AUTH_ME_URL);
    if (me.status === 401 || me.status === 403) return null;
    if (me.status >= 200 && me.status < 300) {
      const empty = me.body === null || (Array.isArray(me.body) && me.body.length === 0);
      // An answer with a person in it is final, role or not. An empty one says nothing.
      if (!empty) return sessionFromAuthMe(me.body);
    }
    const api = await read(API_ME_URL);
    return api.status >= 200 && api.status < 300 ? sessionFromApiMe(api.body) : null;
  } catch {
    return null; // no answer: treated as signed out, never as signed in
  }
}

export function createEntraAuthProvider(options: EntraOptions = {}): AuthProvider {
  const listeners = new Set<() => void>();
  // undefined: not known yet. null: nobody signed in, or no role in this application.
  let session: Session | null | undefined;
  let loading = false;

  const notify = () => listeners.forEach((listener) => listener());

  function load(): void {
    if (loading || session !== undefined || typeof window === "undefined") return;
    loading = true;
    void loadSession(options.fetchImpl ?? fetch).then((loaded) => {
      session = loaded;
      loading = false;
      notify();
    });
  }

  return {
    mode: "entra",
    getSession() {
      load();
      return session;
    },
    authHeaders: () => ({}),
    signOut() {
      session = null;
      notify();
      const navigate = options.navigate ?? ((url: string) => window.location.assign(url));
      navigate(SIGN_OUT_URL);
    },
    subscribe(listener) {
      listeners.add(listener);
      load();
      return () => listeners.delete(listener);
    },
  };
}

export const entraAuthProvider: AuthProvider = createEntraAuthProvider();
