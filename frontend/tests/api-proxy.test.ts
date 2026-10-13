// @vitest-environment node
//
// The frontend's own /api proxy (lib/proxy/forward.ts and app/api/[...path]/route.ts).

import { describe, expect, test, vi } from "vitest";
import * as route from "@/app/api/[...path]/route";
import { ApiError, describeError } from "@/lib/api/errors";
import {
  MULTIPART_ALLOWANCE_BYTES,
  apiProxyTarget,
  backendUrl,
  forwardToBackend,
  identityHeaders,
  maxUploadMb,
  requestHeadersFor,
  responseHeadersFor,
} from "@/lib/proxy/forward";

const MB = 1024 * 1024;
const ENV = { API_PROXY_TARGET: "http://backend.internal:8000" };

interface Seen {
  url: string;
  init: RequestInit & { duplex?: string };
  /** Bytes the backend actually received, read from the forwarded stream. */
  received: number;
}

/** A backend that records what it was sent and answers with `respond`. */
function backend(respond: () => Response = () => Response.json({ ok: true })) {
  const seen: Seen[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const entry: Seen = { url: String(input), init: (init ?? {}) as Seen["init"], received: 0 };
    seen.push(entry);
    if (init?.body) {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      for (;;) {
        const { done, value } = await reader.read(); // throws if the proxy cut the stream
        if (done) break;
        entry.received += value.byteLength;
      }
    }
    return respond();
  });
  return { fetchImpl: fetchImpl as unknown as typeof fetch, calls: fetchImpl, seen };
}

function forwarded(seen: Seen[]): Record<string, string> {
  return seen[0].init.headers as Record<string, string>;
}

function stream(totalBytes: number, chunkBytes = 256 * 1024): ReadableStream<Uint8Array> {
  let sent = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (sent >= totalBytes) return controller.close();
      const size = Math.min(chunkBytes, totalBytes - sent);
      sent += size;
      controller.enqueue(new Uint8Array(size));
    },
  });
}

function upload(bytes: number, headers: Record<string, string> = {}): Request {
  return new Request("http://frontend.test/api/invoices", {
    method: "POST",
    headers: { "content-type": "multipart/form-data; boundary=x", ...headers },
    body: stream(bytes),
    duplex: "half",
  } as RequestInit);
}

describe("where requests go", () => {
  test("the target is read from the environment on every request, not fixed by the build", async () => {
    const { fetchImpl, seen } = backend();
    const request = () => new Request("http://frontend.test/api/me");
    await forwardToBackend(request(), ["me"], { env: { API_PROXY_TARGET: "http://one:8000" }, fetchImpl });
    await forwardToBackend(request(), ["me"], { env: { API_PROXY_TARGET: "http://two:9000/" }, fetchImpl });
    await forwardToBackend(request(), ["me"], { env: {}, fetchImpl });
    expect(seen.map((s) => s.url)).toEqual([
      "http://one:8000/api/me",
      "http://two:9000/api/me",
      "http://localhost:8000/api/me",
    ]);
  });

  test("it follows process.env when it changes between requests", async () => {
    const { fetchImpl, seen } = backend();
    const original = process.env.API_PROXY_TARGET;
    try {
      process.env.API_PROXY_TARGET = "http://first:8000";
      await forwardToBackend(new Request("http://frontend.test/api/me"), ["me"], { fetchImpl });
      process.env.API_PROXY_TARGET = "http://second:8000";
      await forwardToBackend(new Request("http://frontend.test/api/me"), ["me"], { fetchImpl });
    } finally {
      if (original === undefined) delete process.env.API_PROXY_TARGET;
      else process.env.API_PROXY_TARGET = original;
    }
    expect(seen.map((s) => s.url)).toEqual(["http://first:8000/api/me", "http://second:8000/api/me"]);
  });

  test("method, path and query are forwarded", async () => {
    const { fetchImpl, seen } = backend();
    const request = new Request("http://frontend.test/api/invoices?status=HELD_FOR_REVIEW&x=a%20b");
    await forwardToBackend(request, ["invoices"], { env: ENV, fetchImpl });
    expect(seen[0].url).toBe("http://backend.internal:8000/api/invoices?status=HELD_FOR_REVIEW&x=a%20b");
    expect(seen[0].init.method).toBe("GET");
    expect(seen[0].init.body).toBeUndefined();

    const put = new Request("http://frontend.test/api/parameters", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ values: {}, comment: "c" }),
    });
    await forwardToBackend(put, ["parameters"], { env: ENV, fetchImpl });
    expect(seen[1].init.method).toBe("PUT");
    expect(seen[1].url).toBe("http://backend.internal:8000/api/parameters");
  });

  test("path segments are encoded, so a segment cannot change the route", () => {
    expect(backendUrl("http://b", ["invoices", "7", "document"], "")).toBe("http://b/api/invoices/7/document");
    expect(backendUrl("http://b", ["invoices", "../../admin"], "")).toBe("http://b/api/invoices/..%2F..%2Fadmin");
    expect(backendUrl("http://b", ["a b", "c?d"], "?q=1")).toBe("http://b/api/a%20b/c%3Fd?q=1");
  });

  test("defaults", () => {
    expect(apiProxyTarget({})).toBe("http://localhost:8000");
    expect(apiProxyTarget({ API_PROXY_TARGET: "" })).toBe("http://localhost:8000");
    expect(maxUploadMb({})).toBe(10);
    expect(maxUploadMb({ MAX_UPLOAD_MB: "25" })).toBe(25);
    expect(maxUploadMb({ MAX_UPLOAD_MB: "nonsense" })).toBe(10);
    expect(maxUploadMb({ MAX_UPLOAD_MB: "0" })).toBe(10);
  });
});

describe("headers", () => {
  test("identity is forwarded, from the one function that decides it", async () => {
    const { fetchImpl, seen } = backend();
    const request = new Request("http://frontend.test/api/me", {
      headers: { "X-Dev-User": "ana.analyst", "X-Dev-Role": "AP_ANALYST" },
    });
    await forwardToBackend(request, ["me"], { env: ENV, fetchImpl });
    expect(forwarded(seen)["x-dev-user"]).toBe("ana.analyst");
    expect(forwarded(seen)["x-dev-role"]).toBe("AP_ANALYST");
    expect(identityHeaders(request.headers)).toEqual({ "x-dev-user": "ana.analyst", "x-dev-role": "AP_ANALYST" });
    expect(identityHeaders(new Headers())).toEqual({});
  });

  test("content type and accept are forwarded", () => {
    const headers = requestHeadersFor(
      new Headers({ "Content-Type": "multipart/form-data; boundary=abc", Accept: "application/json" }),
    );
    expect(headers).toEqual({ "content-type": "multipart/form-data; boundary=abc", accept: "application/json" });
  });

  test("Host, hop-by-hop headers, cookies and credentials are not forwarded", async () => {
    const { fetchImpl, seen } = backend();
    const request = new Request("http://frontend.test/api/me", {
      headers: {
        Host: "frontend.test",
        Connection: "keep-alive, Upgrade",
        "Keep-Alive": "timeout=5",
        "Transfer-Encoding": "chunked",
        Upgrade: "websocket",
        TE: "trailers",
        Trailer: "Expires",
        "Proxy-Authorization": "Basic abc",
        "Proxy-Authenticate": "Basic",
        Cookie: "session=secret",
        Authorization: "Bearer secret",
        "X-Forwarded-For": "10.0.0.1",
        Origin: "http://frontend.test",
        "X-Dev-User": "ana",
        "X-Dev-Role": "AP_ANALYST",
      },
    });
    await forwardToBackend(request, ["me"], { env: ENV, fetchImpl });
    expect(Object.keys(forwarded(seen)).sort()).toEqual(["x-dev-role", "x-dev-user"]);
  });

  test("the declared length is not copied: the body is streamed and framed by the client", () => {
    expect(requestHeadersFor(new Headers({ "Content-Length": "123", "Content-Type": "application/json" }))).toEqual({
      "content-type": "application/json",
    });
  });

  test("response headers pass through, minus hop-by-hop and CORS", () => {
    const headers = responseHeadersFor(
      new Headers({
        "Content-Type": "application/pdf",
        "Content-Disposition": 'inline; filename="invoice.pdf"',
        "Content-Length": "2411",
        Connection: "keep-alive",
        "Keep-Alive": "timeout=5",
        "Transfer-Encoding": "chunked",
        "Access-Control-Allow-Origin": "http://localhost:3000",
      }),
    );
    expect(Object.fromEntries(headers)).toEqual({
      "content-type": "application/pdf",
      "content-disposition": 'inline; filename="invoice.pdf"',
      "content-length": "2411",
    });
  });

  test("a body that fetch already decompressed loses its encoding and length headers", () => {
    const headers = responseHeadersFor(
      new Headers({ "Content-Type": "application/json", "Content-Encoding": "gzip", "Content-Length": "40" }),
    );
    expect(Object.fromEntries(headers)).toEqual({ "content-type": "application/json" });
  });
});

describe("body", () => {
  test("the body is forwarded as a stream, not buffered", async () => {
    const { fetchImpl, seen } = backend(() => Response.json({ id: 1 }, { status: 202 }));
    const response = await forwardToBackend(upload(3 * MB), ["invoices"], { env: ENV, fetchImpl });
    expect(response.status).toBe(202);
    expect(seen[0].init.body).toBeInstanceOf(ReadableStream);
    expect(seen[0].init.duplex).toBe("half");
    expect(seen[0].received).toBe(3 * MB);
  });

  test("a JSON body arrives intact", async () => {
    let received = "";
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      received = await new Response(init!.body as ReadableStream).text();
      return Response.json({ ok: true });
    });
    const request = new Request("http://frontend.test/api/invoices/1/decisions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision: "APPROVE", comment: "Agreed. ñ €" }),
    });
    await forwardToBackend(request, ["invoices", "1", "decisions"], { env: ENV, fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(JSON.parse(received)).toEqual({ decision: "APPROVE", comment: "Agreed. ñ €" });
  });
});

describe("upload limit", () => {
  const TOO_LARGE = { error: { code: "FILE_TOO_LARGE", message: "The file is larger than the 10 MB limit." } };

  test("a declared length over the limit is refused with 413 and nothing is sent to the backend", async () => {
    const { fetchImpl, calls } = backend();
    const request = upload(1024, { "content-length": String(17 * MB) });
    const response = await forwardToBackend(request, ["invoices"], { env: ENV, fetchImpl });
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual(TOO_LARGE); // same shape and text as the backend
    expect(calls).not.toHaveBeenCalled();
  });

  test("a body within the limit is forwarded", async () => {
    const { fetchImpl, seen } = backend(() => Response.json({ id: 1 }, { status: 202 }));
    const response = await forwardToBackend(upload(9 * MB, { "content-length": String(9 * MB) }), ["invoices"], {
      env: ENV,
      fetchImpl,
    });
    expect(response.status).toBe(202);
    expect(seen[0].received).toBe(9 * MB);
  });

  test("just over the limit goes to the backend, which measures the file itself and answers", async () => {
    // 10 MB + 1 byte of file plus the multipart envelope: inside the allowance.
    const bytes = 10 * MB + 1 + 300;
    const { fetchImpl, seen } = backend(() => Response.json(TOO_LARGE, { status: 413 }));
    const response = await forwardToBackend(upload(bytes, { "content-length": String(bytes) }), ["invoices"], {
      env: ENV,
      fetchImpl,
    });
    expect(seen[0].received).toBe(bytes);
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual(TOO_LARGE);
  });

  test("the boundary is the limit plus the multipart allowance", async () => {
    const edge = 10 * MB + MULTIPART_ALLOWANCE_BYTES;
    const inside = backend();
    await forwardToBackend(upload(16, { "content-length": String(edge) }), ["invoices"], { env: ENV, fetchImpl: inside.fetchImpl });
    expect(inside.calls).toHaveBeenCalledTimes(1);

    const outside = backend();
    const refused = await forwardToBackend(upload(16, { "content-length": String(edge + 1) }), ["invoices"], {
      env: ENV,
      fetchImpl: outside.fetchImpl,
    });
    expect(refused.status).toBe(413);
    expect(outside.calls).not.toHaveBeenCalled();
  });

  test("a body with no declared length is cut off once it passes the limit", async () => {
    const { fetchImpl, seen } = backend();
    const response = await forwardToBackend(upload(17 * MB), ["invoices"], { env: ENV, fetchImpl });
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual(TOO_LARGE);
    expect(seen[0].received).toBeLessThanOrEqual(10 * MB + MULTIPART_ALLOWANCE_BYTES);
  });

  test("a declared length that understates the body does not get it through", async () => {
    const { fetchImpl } = backend();
    const response = await forwardToBackend(upload(17 * MB, { "content-length": "1024" }), ["invoices"], {
      env: ENV,
      fetchImpl,
    });
    expect(response.status).toBe(413);
  });

  test("the limit follows MAX_UPLOAD_MB, the backend's own setting", async () => {
    const { fetchImpl, calls } = backend();
    const env = { ...ENV, MAX_UPLOAD_MB: "2" };
    const response = await forwardToBackend(upload(16, { "content-length": String(3 * MB) }), ["invoices"], { env, fetchImpl });
    expect(response.status).toBe(413);
    expect((await response.json()).error.message).toBe("The file is larger than the 2 MB limit.");
    expect(calls).not.toHaveBeenCalled();
  });

  test("requests without a body are never limited", async () => {
    const { fetchImpl, calls } = backend();
    const request = new Request("http://frontend.test/api/invoices/1/document", {
      headers: { "content-length": String(99 * MB) },
    });
    expect((await forwardToBackend(request, ["invoices", "1", "document"], { env: ENV, fetchImpl })).status).toBe(200);
    expect(calls).toHaveBeenCalledTimes(1);
  });
});

describe("backend failures", () => {
  const failures: Array<[string, Error]> = [
    ["connection refused", Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNREFUSED", address: "10.0.4.17", port: 8000 } })],
    ["name not resolved", Object.assign(new TypeError("fetch failed"), { cause: { code: "ENOTFOUND", hostname: "backend.internal" } })],
    ["timeout", Object.assign(new Error("The operation was aborted due to timeout"), { name: "TimeoutError" })],
  ];

  test.each(failures)("%s gives a 502 with a stable code and no internal detail", async (_label, error) => {
    const log = vi.fn();
    const fetchImpl = vi.fn(async () => {
      throw error;
    }) as unknown as typeof fetch;
    const response = await forwardToBackend(new Request("http://frontend.test/api/me"), ["me"], { env: ENV, fetchImpl, log });

    expect(response.status).toBe(502);
    const body = await response.json();
    expect(body).toEqual({
      error: { code: "BACKEND_UNAVAILABLE", message: "The service is temporarily unavailable. Try again in a moment." },
    });
    const text = JSON.stringify(body);
    for (const internal of ["backend.internal", "10.0.4.17", "8000", "ECONNREFUSED", "ENOTFOUND", "fetch failed", "timeout"]) {
      expect(text).not.toContain(internal);
    }
    expect(log).toHaveBeenCalledTimes(1); // the detail goes to the server log
    expect(log.mock.calls[0][0]).toContain("API proxy: no answer from the backend");
  });

  test("the screens show that code with their own text", () => {
    const description = describeError(new ApiError(502, "BACKEND_UNAVAILABLE", "anything"));
    expect(description).toEqual({
      code: "BACKEND_UNAVAILABLE",
      title: "Service unavailable",
      message: "The service is temporarily unavailable. Try again in a moment.",
    });
  });
});

describe("responses", () => {
  test("status, body and headers of the backend come back unchanged", async () => {
    const { fetchImpl } = backend(
      () => new Response("%PDF-1.7 content", { status: 200, headers: { "Content-Type": "application/pdf", "Content-Disposition": 'inline; filename="a.pdf"' } }),
    );
    const response = await forwardToBackend(new Request("http://frontend.test/api/invoices/1/document"), ["invoices", "1", "document"], { env: ENV, fetchImpl });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/pdf");
    expect(response.headers.get("content-disposition")).toBe('inline; filename="a.pdf"');
    expect(await response.text()).toBe("%PDF-1.7 content");
  });

  test.each([401, 403, 404, 409, 422])("a %i from the backend is passed on with its error body", async (status) => {
    const error = { error: { code: "SOME_CODE", message: "Written by the backend." } };
    const { fetchImpl } = backend(() => Response.json(error, { status }));
    const response = await forwardToBackend(new Request("http://frontend.test/api/x"), ["x"], { env: ENV, fetchImpl });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(error);
  });

  test("a redirect from the backend is passed on, not followed", async () => {
    const { fetchImpl, seen } = backend(() => new Response(null, { status: 307, headers: { Location: "/api/elsewhere" } }));
    const response = await forwardToBackend(new Request("http://frontend.test/api/x"), ["x"], { env: ENV, fetchImpl });
    expect(seen[0].init.redirect).toBe("manual");
    expect(response.status).toBe(307);
  });
});

describe("route handler", () => {
  test("handles every method the API uses, on the Node.js runtime, per request", () => {
    for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const) {
      expect(typeof route[method]).toBe("function");
    }
    expect(route.runtime).toBe("nodejs");
    expect(route.dynamic).toBe("force-dynamic");
  });

  test("passes the path segments of the request to the proxy", async () => {
    const original = { fetch: globalThis.fetch, target: process.env.API_PROXY_TARGET };
    const seen: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      seen.push(String(input));
      return Response.json({ ok: true });
    }) as typeof fetch;
    process.env.API_PROXY_TARGET = "http://backend.internal:8000";
    try {
      const response = await route.GET(new Request("http://frontend.test/api/invoices/7/audit?x=1"), {
        params: Promise.resolve({ path: ["invoices", "7", "audit"] }),
      });
      expect(response.status).toBe(200);
      expect(seen).toEqual(["http://backend.internal:8000/api/invoices/7/audit?x=1"]);
    } finally {
      globalThis.fetch = original.fetch;
      if (original.target === undefined) delete process.env.API_PROXY_TARGET;
      else process.env.API_PROXY_TARGET = original.target;
    }
  });
});
