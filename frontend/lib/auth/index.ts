// The single entry point for identity. Screens and the API client use only what this
// file exports; none of them knows about the X-Dev-* headers or about Azure.
//
// NEXT_PUBLIC_AUTH_MODE chooses the provider: "entra" is Microsoft Entra ID through Azure
// Container Apps authentication; anything else, or nothing, is the development login.
// Next.js fixes NEXT_PUBLIC_* values WHEN THE APP IS BUILT, so the container image is
// built with NEXT_PUBLIC_AUTH_MODE=entra (frontend/Dockerfile) and a local "npm run dev"
// uses the development login.

"use client";

import { useSyncExternalStore } from "react";
import { cache } from "@/lib/cache";
import { devAuthProvider } from "./dev";
import { entraAuthProvider } from "./entra";
import type { AuthProvider, Session } from "./types";

export type { AuthProvider, Session } from "./types";

export const auth: AuthProvider =
  process.env.NEXT_PUBLIC_AUTH_MODE === "entra" ? entraAuthProvider : devAuthProvider;

// What one person loaded must never be shown to another (DL-80). Whenever the signed-in
// person or their role changes, including signing out, everything loaded so far is dropped.
// It is done here, for whichever provider is in use, so no screen has to remember it.
const identityOf = (session: Session | null | undefined) => (session ? `${session.role}:${session.username}` : "");
let identity = identityOf(auth.getSession());
auth.subscribe(() => {
  const next = identityOf(auth.getSession());
  if (next === identity) return;
  identity = next;
  cache.clear();
});

/** True while the app runs with the development login. The UI must say so. */
export const isDevelopmentLogin = auth.mode === "development";

/**
 * The signed-in user, or null. `undefined` means "not known yet": on the server and
 * during the first client render, before the browser storage can be read.
 */
export function useSession(): Session | null | undefined {
  return useSyncExternalStore(
    auth.subscribe,
    () => auth.getSession(),
    () => undefined, // on the server, and for the first render in the browser
  );
}
