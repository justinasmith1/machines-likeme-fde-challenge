// Server-side proxy from the frontend's own origin to the backend API.
//
// With API_BASE_URL=/api the browser talks only to the frontend. This forwards each
// /api/* request to the backend, whose address is read from the environment ON EVERY
// REQUEST, so one build serves any environment and the backend can stay private: only
// the frontend server needs to reach it.
//
// It forwards the method, path, query and body (streamed, never held in memory) and a
// fixed list of headers. It does not forward Host, hop-by-hop headers, cookies or
// anything else not listed here.

export const DEFAULT_API_PROXY_TARGET = "http://localhost:8000";
export const DEFAULT_MAX_UPLOAD_MB = 10;

/**
 * Room for the multipart envelope around a file (boundaries and part headers), so a file
 * of exactly the limit is not refused here. A body between the limit and the limit plus
 * this allowance is forwarded, and the backend, which measures the file itself, answers.
 */
export const MULTIPART_ALLOWANCE_BYTES = 64 * 1024;

const BACKEND_TIMEOUT_MS = 60_000;

type Env = Record<string, string | undefined>;

/** Backend origin, without /api. Read per request: changing it needs no rebuild. */
export function apiProxyTarget(env: Env = process.env): string {
  return (env.API_PROXY_TARGET || DEFAULT_API_PROXY_TARGET).replace(/\/+$/, "");
}

/** The same setting the backend uses, so one value configures both. */
export function maxUploadMb(env: Env = process.env): number {
  const value = Number(env.MAX_UPLOAD_MB);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_MAX_UPLOAD_MB;
}

/**
 * THE ONE PLACE that decides which identity travels to the backend.
 *
 * Development: the X-Dev-* headers set by lib/auth/dev. The backend honours them only
 * when it runs with APP_ENV=development.
 *
 * Entra (NEXT_PUBLIC_AUTH_MODE=entra): the X-MS-CLIENT-PRINCIPAL header that Azure
 * Container Apps authentication adds in front of this server after a Microsoft Entra ID
 * sign-in. Azure does not let a request from outside set it, so what arrives here is the
 * platform's. The X-Dev-* headers are never forwarded in this mode, whatever a client
 * sends. The backend reads the header when it runs with APP_ENV=entra.
 *
 * In both modes no cookie and no Authorization header is forwarded.
 */
const DEVELOPMENT_IDENTITY_HEADERS = ["x-dev-user", "x-dev-role"];
const ENTRA_IDENTITY_HEADERS = ["x-ms-client-principal"];

/** Read from the environment of the running server, like the other proxy settings. */
export function proxyAuthMode(env: Env = process.env): "entra" | "development" {
  // Looked up by name at run time (Next.js only fixes the literal process.env.NEXT_PUBLIC_*
  // form at build time), so the container's environment decides.
  const name = "NEXT_PUBLIC_AUTH_MODE";
  return env[name] === "entra" ? "entra" : "development";
}

export function identityHeaders(incoming: Headers, env: Env = process.env): Record<string, string> {
  const names = proxyAuthMode(env) === "entra" ? ENTRA_IDENTITY_HEADERS : DEVELOPMENT_IDENTITY_HEADERS;
  const forwarded: Record<string, string> = {};
  for (const name of names) {
    const value = incoming.get(name);
    if (value !== null) forwarded[name] = value;
  }
  return forwarded;
}

/** Request headers the backend needs, besides identity. Everything else is dropped. */
const FORWARDED_REQUEST_HEADERS = ["accept", "accept-language", "content-type"];

/** Never passed on in either direction (RFC 9110, connection-specific headers). */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

export function requestHeadersFor(incoming: Headers, env: Env = process.env): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value !== null) headers[name] = value;
  }
  // Content-Length is not copied: the body is streamed, and the HTTP client frames it.
  return { ...headers, ...identityHeaders(incoming, env) };
}

export function responseHeadersFor(upstream: Headers): Headers {
  const headers = new Headers();
  // fetch has already decoded a compressed body, so its encoding and length no longer apply.
  const decoded = upstream.has("content-encoding");
  upstream.forEach((value, name) => {
    const key = name.toLowerCase();
    if (HOP_BY_HOP.has(key)) return;
    if (key.startsWith("access-control-")) return; // same origin: CORS does not apply
    if (key === "content-encoding") return;
    if (key === "content-length" && decoded) return;
    headers.set(name, value);
  });
  return headers;
}

/** Errors in the backend's own format: {"error": {"code", "message"}}. */
function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

function tooLarge(limitMb: number): Response {
  // Same code and wording as the backend, so the screens treat both the same way.
  return errorResponse(413, "FILE_TOO_LARGE", `The file is larger than the ${limitMb} MB limit.`);
}

function backendUnavailable(): Response {
  return errorResponse(
    502,
    "BACKEND_UNAVAILABLE",
    "The service is temporarily unavailable. Try again in a moment.",
  );
}

export function backendUrl(target: string, path: string[], search: string): string {
  const suffix = path.map((segment) => encodeURIComponent(segment)).join("/");
  return `${target}/api/${suffix}${search}`;
}

export interface ForwardOptions {
  env?: Env;
  fetchImpl?: typeof fetch;
  log?: (message: string) => void;
}

/**
 * Forward one request to the backend and return its response.
 * `path` is the part after /api, already split into segments.
 */
export async function forwardToBackend(
  request: Request,
  path: string[],
  options: ForwardOptions = {},
): Promise<Response> {
  const env = options.env ?? process.env;
  const doFetch = options.fetchImpl ?? fetch;
  const log = options.log ?? ((message: string) => console.error(message));

  const limitMb = maxUploadMb(env);
  const maxBodyBytes = limitMb * 1024 * 1024 + MULTIPART_ALLOWANCE_BYTES;
  const hasBody = request.body !== null && request.method !== "GET" && request.method !== "HEAD";

  // Refuse an oversized upload from its declared length, before reading or sending a byte.
  const declared = Number(request.headers.get("content-length"));
  if (hasBody && Number.isFinite(declared) && declared > maxBodyBytes) {
    return tooLarge(limitMb);
  }

  // A body with no declared length (or a wrong one) is counted while it streams through.
  let exceeded = false;
  let body: ReadableStream<Uint8Array> | undefined;
  if (hasBody) {
    let seen = 0;
    body = request.body!.pipeThrough(
      new TransformStream<Uint8Array, Uint8Array>({
        transform(chunk, controller) {
          seen += chunk.byteLength;
          if (seen > maxBodyBytes) {
            exceeded = true;
            controller.error(new Error("request body over the upload limit"));
            return;
          }
          controller.enqueue(chunk);
        },
      }),
    );
  }

  const url = backendUrl(apiProxyTarget(env), path, new URL(request.url).search);
  let upstream: Response;
  try {
    upstream = await doFetch(url, {
      method: request.method,
      headers: requestHeadersFor(request.headers, env),
      body,
      // Required by Node to send a streamed request body.
      ...(body ? { duplex: "half" } : {}),
      redirect: "manual",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(BACKEND_TIMEOUT_MS)]),
    } as RequestInit);
  } catch (error) {
    if (exceeded) return tooLarge(limitMb);
    // The detail stays in the server log. The caller gets a stable code and plain text.
    const name = error instanceof Error ? error.name : "unknown";
    const cause = (error as { cause?: { code?: unknown } } | null)?.cause?.code;
    log(`API proxy: no answer from the backend (${name}${typeof cause === "string" ? `, ${cause}` : ""})`);
    return backendUnavailable();
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeadersFor(upstream.headers),
  });
}
