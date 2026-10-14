import type { Role } from "@/lib/api/types";

export interface Session {
  username: string;
  role: Role;
}

/**
 * What the rest of the app may know about identity. Two implementations: the development
 * login (lib/auth/dev) and Microsoft Entra ID through Azure (lib/auth/entra).
 */
export interface AuthProvider {
  mode: "development" | "entra";
  /** Null: nobody is signed in. Undefined: not known yet (still being asked). */
  getSession(): Session | null | undefined;
  /** Headers that authenticate a request to the API. Empty when a cookie does it. */
  authHeaders(): Record<string, string>;
  signOut(): void;
  subscribe(listener: () => void): () => void;
}
