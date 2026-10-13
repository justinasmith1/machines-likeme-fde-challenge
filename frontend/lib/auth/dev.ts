// DEVELOPMENT ONLY. Identity is whatever the person picks on the login screen, kept in
// sessionStorage and sent as the X-Dev-User / X-Dev-Role headers. This is a convenience
// for local work, not security: the backend honours these headers only when it runs with
// APP_ENV=development, and answers 401 otherwise (DL-55).
//
// Nothing outside lib/auth may import this file, except the development login screen.

import type { Role } from "@/lib/api/types";
import type { AuthProvider, Session } from "./types";

const STORAGE_KEY = "contoso.dev-session";
const ROLES: Role[] = ["INTAKE", "AP_ANALYST", "AP_MANAGER"];

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cachedSession: Session | null = null;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // storage blocked by the browser
  }
}

function parse(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<Session>;
    if (typeof value.username === "string" && value.username.trim() && ROLES.includes(value.role as Role)) {
      return { username: value.username, role: value.role as Role };
    }
  } catch {
    // corrupt value: treated as signed out
  }
  return null;
}

function getSession(): Session | null {
  const raw = storage()?.getItem(STORAGE_KEY) ?? null;
  if (raw !== cachedRaw) {
    // Keep the same object while nothing changed, so React does not re-render in a loop.
    cachedRaw = raw;
    cachedSession = parse(raw);
  }
  return cachedSession;
}

function notify(): void {
  listeners.forEach((listener) => listener());
}

/** Used by the development login screen only. */
export function devSignIn(username: string, role: Role): Session {
  const name = username.trim();
  if (!name) throw new Error("A user name is required.");
  if (!ROLES.includes(role)) throw new Error("Unknown role.");
  const session: Session = { username: name, role };
  storage()?.setItem(STORAGE_KEY, JSON.stringify(session));
  notify();
  return session;
}

export const devAuthProvider: AuthProvider = {
  mode: "development",
  getSession,
  authHeaders(): Record<string, string> {
    const session = getSession();
    return session ? { "X-Dev-User": session.username, "X-Dev-Role": session.role } : {};
  },
  signOut() {
    storage()?.removeItem(STORAGE_KEY);
    notify();
  },
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export const DEV_ROLES = ROLES;
