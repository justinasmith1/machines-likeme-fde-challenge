// The only place that talks to the backend. Screens call these methods; they never use
// fetch and never see how the request is authenticated.
//
// baseUrl may be absolute ("http://localhost:8000/api") or relative ("/api"). A relative
// base is for a deployment where the frontend and the API share an origin, so the
// session cookie travels with no tokens and no CORS.

import { ApiError } from "./errors";
import type {
  AuditEvent,
  Corrections,
  DecisionName,
  IntakeInvoice,
  InvoiceDetail,
  InvoiceStatus,
  InvoiceSummary,
  Me,
  RuleParameters,
  ReviewerInvoice,
  UploadReceipt,
} from "./types";

export interface ApiClientOptions {
  baseUrl: string;
  /** Headers that identify the caller. Supplied by lib/auth; empty when not needed. */
  authHeaders: () => Record<string, string>;
  fetchImpl?: typeof fetch;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT";
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | undefined>;
}

export type ApiClient = ReturnType<typeof createApiClient>;

export function joinUrl(baseUrl: string, path: string, query?: RequestOptions["query"]): string {
  const base = baseUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  const pairs = Object.entries(query ?? {}).filter(([, value]) => value !== undefined && value !== "");
  const search = pairs.length
    ? `?${pairs.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value as string)}`).join("&")}`
    : "";
  return `${base}${suffix}${search}`;
}

export function createApiClient(options: ApiClientOptions) {
  const doFetch = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));

  async function send(path: string, request: RequestOptions = {}): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", ...options.authHeaders() };
    let body: BodyInit | undefined;
    if (request.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(request.json);
    } else if (request.form) {
      body = request.form; // the browser sets the multipart boundary
    }

    let response: Response;
    try {
      response = await doFetch(joinUrl(options.baseUrl, path, request.query), {
        method: request.method ?? "GET",
        headers,
        body,
      });
    } catch {
      throw new ApiError(0, "NETWORK_ERROR", "The service cannot be reached.");
    }
    if (!response.ok) {
      throw await toApiError(response);
    }
    return response;
  }

  async function getJson<T>(path: string, request?: RequestOptions): Promise<T> {
    const response = await send(path, request);
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError(response.status, "UNEXPECTED_ERROR", "The service returned an unreadable answer.");
    }
  }

  return {
    me: () => getJson<Me>("/me"),

    listInvoices: async (status?: InvoiceStatus | "") =>
      (await getJson<{ invoices: Array<InvoiceSummary | IntakeInvoice> }>("/invoices", { query: { status } }))
        .invoices,

    getInvoice: (id: number) => getJson<InvoiceDetail>(`/invoices/${id}`),

    getDocument: async (id: number): Promise<Blob> => (await send(`/invoices/${id}/document`)).blob(),

    getAudit: async (id: number) =>
      (await getJson<{ events: AuditEvent[] }>(`/invoices/${id}/audit`)).events,

    uploadInvoice: (file: File) => {
      const form = new FormData();
      form.append("file", file, file.name);
      return getJson<UploadReceipt>("/invoices", { method: "POST", form });
    },

    retryExtraction: (id: number) => getJson<UploadReceipt>(`/invoices/${id}/retry`, { method: "POST" }),

    correctExtraction: (id: number, corrections: Corrections, comment: string) =>
      getJson<ReviewerInvoice>(`/invoices/${id}/corrections`, {
        method: "POST",
        json: { corrections, comment },
      }),

    decide: (id: number, decision: DecisionName, comment: string) =>
      getJson<ReviewerInvoice>(`/invoices/${id}/decisions`, {
        method: "POST",
        json: { decision, comment },
      }),

    reviewQueue: async () => (await getJson<{ invoices: InvoiceSummary[] }>("/review-queue")).invoices,

    getParameters: () => getJson<RuleParameters>("/parameters"),

    updateParameters: (values: Record<string, unknown>, comment: string) =>
      getJson<RuleParameters>("/parameters", { method: "PUT", json: { values, comment } }),

    parameterHistory: async () => (await getJson<{ versions: RuleParameters[] }>("/parameters/history")).versions,
  };
}

/** The backend answers errors as {"error": {"code", "message"}}. Anything else is unexpected. */
async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } };
    const code = body?.error?.code;
    const message = body?.error?.message;
    if (typeof code === "string" && typeof message === "string") {
      return new ApiError(response.status, code, message);
    }
  } catch {
    // not JSON: fall through
  }
  return new ApiError(response.status, "UNEXPECTED_ERROR", `The service answered with status ${response.status}.`);
}
