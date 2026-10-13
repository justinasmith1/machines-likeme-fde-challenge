import type { Role } from "@/lib/api/types";

export interface Session {
  username: string;
  role: Role;
}

/** What the rest of the app may know about identity. Phase 6 provides another implementation. */
export interface AuthProvider {
  mode: "development" | "entra";
  getSession(): Session | null;
  /** Headers that authenticate a request to the API. Empty when a cookie does it. */
  authHeaders(): Record<string, string>;
  signOut(): void;
  subscribe(listener: () => void): () => void;
}
