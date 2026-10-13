"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { createApiClient, type ApiClient } from "@/lib/api/client";
import { auth } from "@/lib/auth";

const ApiContext = createContext<ApiClient | null>(null);

interface ProvidersProps {
  /** Resolved by the server at runtime, so one build works in every environment. */
  apiBaseUrl: string;
  /** Tests pass their own client. */
  client?: ApiClient;
  children: ReactNode;
}

export function Providers({ apiBaseUrl, client, children }: ProvidersProps) {
  const value = useMemo(
    () => client ?? createApiClient({ baseUrl: apiBaseUrl, authHeaders: () => auth.authHeaders() }),
    [apiBaseUrl, client],
  );
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiClient {
  const client = useContext(ApiContext);
  if (!client) throw new Error("useApi must be used inside <Providers>.");
  return client;
}
