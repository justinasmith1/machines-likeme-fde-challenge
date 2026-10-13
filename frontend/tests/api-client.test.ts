import { describe, expect, test, vi } from "vitest";
import { createApiClient, joinUrl } from "@/lib/api/client";
import { ApiError, describeError, isApiError } from "@/lib/api/errors";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function clientWith(fetchImpl: typeof fetch, baseUrl = "http://localhost:8000/api") {
  return createApiClient({
    baseUrl,
    authHeaders: () => ({ "X-Test-Identity": "someone" }),
    fetchImpl,
  });
}

describe("urls", () => {
  test("absolute base", () => {
    expect(joinUrl("http://localhost:8000/api", "/invoices")).toBe("http://localhost:8000/api/invoices");
    expect(joinUrl("http://localhost:8000/api/", "invoices")).toBe("http://localhost:8000/api/invoices");
  });

  test("relative base, for a deployment where frontend and API share an origin", () => {
    expect(joinUrl("/api", "/invoices/7")).toBe("/api/invoices/7");
    expect(joinUrl("/api/", "/me")).toBe("/api/me");
  });

  test("query parameters are encoded and empty ones dropped", () => {
    expect(joinUrl("/api", "/invoices", { status: "HELD_FOR_REVIEW" })).toBe("/api/invoices?status=HELD_FOR_REVIEW");
    expect(joinUrl("/api", "/invoices", { status: "" })).toBe("/api/invoices");
    expect(joinUrl("/api", "/invoices", { status: undefined })).toBe("/api/invoices");
    expect(joinUrl("/api", "/x", { q: "a b&c" })).toBe("/api/x?q=a%20b%26c");
  });

  test("the client calls a relative base as given", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ username: "ana", role: "AP_ANALYST", upload: { max_mb: 10, extensions: [] } }));
    await clientWith(fetchImpl as unknown as typeof fetch, "/api").me();
    expect(fetchImpl.mock.calls[0][0]).toBe("/api/me");
  });
});

describe("requests", () => {
  test("every request carries the headers supplied by the auth module", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ invoices: [] }));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    await client.listInvoices();
    await client.reviewQueue();
    for (const call of fetchImpl.mock.calls) {
      const init = call[1] as RequestInit;
      expect((init.headers as Record<string, string>)["X-Test-Identity"]).toBe("someone");
    }
  });

  test("the client itself knows nothing about development headers", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ invoices: [] }));
    await createApiClient({ baseUrl: "/api", authHeaders: () => ({}), fetchImpl: fetchImpl as unknown as typeof fetch }).listInvoices();
    const headers = (fetchImpl.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(Object.keys(headers)).toEqual(["Accept"]);
  });

  test("a decision is posted as JSON", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ invoice: { id: 3 } }));
    await clientWith(fetchImpl as unknown as typeof fetch).decide(3, "APPROVE", "Agreed.");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/api/invoices/3/decisions");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "APPROVE", comment: "Agreed." });
  });

  test("an upload is sent as multipart, letting the browser set the content type", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ id: 1, filename: "a.pdf", status: "PENDING_EXTRACTION" }, 202));
    const file = new File(["%PDF"], "a.pdf", { type: "application/pdf" });
    const receipt = await clientWith(fetchImpl as unknown as typeof fetch).uploadInvoice(file);
    const init = fetchImpl.mock.calls[0][1] as RequestInit;
    expect(receipt.status).toBe("PENDING_EXTRACTION");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  test("corrections, parameters and the status filter use the documented shapes", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ invoices: [], versions: [] }));
    const client = clientWith(fetchImpl as unknown as typeof fetch, "/api");
    await client.correctExtraction(5, { header: { currency: "USD" } }, "Misread.");
    await client.updateParameters({ price_tolerance_pct: "0.05" }, "Agreed.");
    await client.listInvoices("AUTO_PAY");
    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls[0][0]).toBe("/api/invoices/5/corrections");
    expect(JSON.parse(calls[0][1].body as string)).toEqual({
      corrections: { header: { currency: "USD" } },
      comment: "Misread.",
    });
    expect(calls[1][1].method).toBe("PUT");
    expect(JSON.parse(calls[1][1].body as string)).toEqual({
      values: { price_tolerance_pct: "0.05" },
      comment: "Agreed.",
    });
    expect(calls[2][0]).toBe("/api/invoices?status=AUTO_PAY");
  });

  test("the document comes back as a blob", async () => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => new Response("%PDF-1.7", { headers: { "Content-Type": "application/pdf" } }));
    const blob = await clientWith(fetchImpl as unknown as typeof fetch).getDocument(2);
    expect(blob.type).toBe("application/pdf");
    expect(await blob.text()).toBe("%PDF-1.7");
  });
});

describe("errors", () => {
  const cases: Array<[number, string]> = [
    [401, "UNAUTHENTICATED"],
    [403, "PERMISSION_DENIED"],
    [404, "NOT_FOUND"],
    [409, "INVALID_STATE"],
    [413, "FILE_TOO_LARGE"],
    [422, "VALIDATION_ERROR"],
    [422, "UNSUPPORTED_DOCUMENT_TYPE"],
  ];

  test.each(cases)("HTTP %i with code %s becomes an ApiError with that code", async (status, code) => {
    const fetchImpl = vi.fn(async (..._args: Parameters<typeof fetch>) => json({ error: { code, message: "Written by the backend." } }, status));
    const failure = await clientWith(fetchImpl as unknown as typeof fetch)
      .getInvoice(1)
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe(code);
    expect((failure as ApiError).status).toBe(status);
    expect(isApiError(failure, code as never)).toBe(true);
  });

  test("an error body that is not the API's JSON shape is an unexpected error", async () => {
    const html = vi.fn(async (..._args: Parameters<typeof fetch>) => new Response("<html>Bad gateway</html>", { status: 502 }));
    const failure = await clientWith(html as unknown as typeof fetch).me().catch((e: unknown) => e);
    expect((failure as ApiError).code).toBe("UNEXPECTED_ERROR");
    expect(describeError(failure).message).not.toContain("html");
  });

  test("a failed connection is a network error, not a raw exception", async () => {
    const down = vi.fn(async (..._args: Parameters<typeof fetch>) => {
      throw new TypeError("Failed to fetch");
    });
    const failure = await clientWith(down as unknown as typeof fetch).me().catch((e: unknown) => e);
    expect((failure as ApiError).code).toBe("NETWORK_ERROR");
    expect(describeError(failure).message).not.toContain("Failed to fetch");
  });

  test("a success answer that is not JSON is an unexpected error", async () => {
    const odd = vi.fn(async (..._args: Parameters<typeof fetch>) => new Response("not json", { status: 200 }));
    const failure = await clientWith(odd as unknown as typeof fetch).me().catch((e: unknown) => e);
    expect((failure as ApiError).code).toBe("UNEXPECTED_ERROR");
  });
});

describe("messages shown to the user", () => {
  test("the backend's explanation is shown for the codes where it is written for the user", () => {
    const limit = new ApiError(
      403,
      "PERMISSION_DENIED",
      "The invoice total (7,559.47) is above the analyst approval limit (5,000.00). The AP Manager must decide.",
    );
    expect(describeError(limit)).toEqual({
      code: "PERMISSION_DENIED",
      title: "Not allowed",
      message: limit.message,
    });
    const state = new ApiError(409, "INVALID_STATE", "Invoice 4 is already AUTO_PAY; the decision would change nothing.");
    expect(describeError(state).title).toBe("This action no longer applies");
    expect(describeError(state).message).toBe(state.message);
    expect(describeError(new ApiError(413, "FILE_TOO_LARGE", "The file is larger than the 10 MB limit.")).message).toBe(
      "The file is larger than the 10 MB limit.",
    );
    expect(
      describeError(new ApiError(422, "UNSUPPORTED_DOCUMENT_TYPE", "File type '.docx' is not supported.")).title,
    ).toBe("File type not supported");
    expect(describeError(new ApiError(422, "VALIDATION_ERROR", "A comment is required.")).message).toBe(
      "A comment is required.",
    );
    expect(describeError(new ApiError(404, "NOT_FOUND", "Invoice 9 does not exist.")).message).toBe(
      "Invoice 9 does not exist.",
    );
  });

  test("an unauthenticated answer gets the app's own text", () => {
    const description = describeError(new ApiError(401, "UNAUTHENTICATED", "Send the X-Dev-User and X-Dev-Role headers."));
    expect(description.title).toBe("Sign-in required");
    expect(description.message).not.toContain("X-Dev");
  });

  test("unknown codes and non-API errors never leak their text", () => {
    const unknown = describeError(new ApiError(500, "DATABASE_EXPLODED", "psycopg2.OperationalError at line 42"));
    expect(unknown.code).toBe("UNEXPECTED_ERROR");
    expect(unknown.message).not.toContain("psycopg2");

    const crash = describeError(new TypeError("Cannot read properties of undefined (reading 'x')"));
    expect(crash.code).toBe("UNEXPECTED_ERROR");
    expect(crash.message).not.toContain("undefined");
    expect(describeError("a string").code).toBe("UNEXPECTED_ERROR");
  });
});
