// The real screens with the cache (DL-80): going back shows what was loaded, an action
// never leaves an old state on screen, and nothing crosses from one person to another.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, test, vi } from "vitest";
import { InvoicesView } from "@/components/InvoicesView";
import { ParametersView } from "@/components/ParametersView";
import { Providers } from "@/components/Providers";
import { ReviewQueueView } from "@/components/ReviewQueueView";
import { InvoiceDetailView } from "@/components/invoice/InvoiceDetailView";
import { auth } from "@/lib/auth";
import { devSignIn } from "@/lib/auth/dev";
import { cache } from "@/lib/cache";
import { keys } from "@/lib/hooks";
import { createFakeApi } from "./fake-api";
import { chooseFile, session, show } from "./screen-helpers";

const SUPPLIER = "Test Supplier B (SUP-002)";
const ANALYST = session("AP_ANALYST");

/** Makes the next call to an API method wait until `release` is called. */
function hold<T>(method: ReturnType<typeof vi.fn>, value?: T) {
  let release: (() => void) | undefined;
  const original = method.getMockImplementation() as (...args: unknown[]) => unknown;
  method.mockImplementationOnce(
    (...args: unknown[]) =>
      new Promise((resolve) => {
        release = () => resolve(value !== undefined ? value : original(...args));
      }),
  );
  return {
    release: async () => {
      await waitFor(() => expect(release).toBeDefined()); // the request has gone out
      await act(async () => release!());
    },
  };
}

/** Other views the person visited earlier, now off screen. */
function seedOtherViews() {
  cache.set(keys.reviewQueue, ["old queue"]);
  cache.set(keys.invoiceList("HELD_FOR_REVIEW"), ["old filtered list"]);
  cache.set(keys.audit(1), ["old audit trail"]);
  cache.set(keys.parameters, "parameters");
}
const expectInvoiceViewsForgotten = () => {
  expect(cache.peek(keys.reviewQueue)).toBeUndefined();
  expect(cache.peek(keys.invoiceList("HELD_FOR_REVIEW"))).toBeUndefined();
  expect(cache.peek(keys.audit(1))).toBeUndefined();
  expect(cache.peek(keys.parameters)!.data).toBe("parameters"); // not affected
};

async function approve(comment = "Checked.") {
  fireEvent.click(await screen.findByLabelText("Approve for payment"));
  fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
    target: { value: comment },
  });
  fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
  await screen.findByText("Decision recorded: Approve for payment.");
}

async function correctQuantity() {
  fireEvent.click(await screen.findByRole("button", { name: "Correct data" }));
  fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#correction-comment" }), {
    target: { value: "Read from the paper copy." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await screen.findByText(/Correction saved\./);
}

describe("going back to a view", () => {
  test("the review queue is on screen at once, without the loading state, and is refreshed", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const first = show(api, <ReviewQueueView />);
    expect(screen.getByText("Loading the review queue…")).toBeTruthy(); // nothing cached yet
    await screen.findByText(SUPPLIER);
    first.unmount();

    const refresh = hold(calls.reviewQueue, []);
    show(api, <ReviewQueueView />);
    expect(screen.getByText(SUPPLIER)).toBeTruthy(); // immediately
    expect(screen.queryByText("Loading the review queue…")).toBeNull();
    await waitFor(() => expect(calls.reviewQueue).toHaveBeenCalledTimes(2));

    await refresh.release(); // the background answer replaces what was shown
    expect(await screen.findByText("Nothing is waiting for review.")).toBeTruthy();
  });

  test("the invoice detail too; its document is requested again", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const first = show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await screen.findByText("Extracted data");
    first.unmount();

    const refresh = hold(calls.getInvoice);
    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    expect(screen.getByText("Extracted data")).toBeTruthy();
    expect(screen.queryByText("Loading the invoice…")).toBeNull();
    await refresh.release();
    expect(calls.getInvoice).toHaveBeenCalledTimes(2);
  });

  test("each list filter is cached on its own", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const first = show(api, <InvoicesView session={ANALYST} />);
    await screen.findByText(SUPPLIER);
    expect(cache.keys()).toContain("invoices:list:all");
    first.unmount();
    hold(calls.listInvoices);
    show(api, <InvoicesView session={ANALYST} />);
    expect(screen.getByText(SUPPLIER)).toBeTruthy();
    expect(screen.queryByText("Loading documents…")).toBeNull();
  });
});

describe("an action never leaves an old state on screen", () => {
  test("decision: back in the queue, the decided invoice is not shown as still waiting", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const queue = show(api, <ReviewQueueView />);
    await screen.findByText(SUPPLIER);
    queue.unmount();

    const detail = show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await approve();
    detail.unmount();

    const refresh = hold(calls.reviewQueue);
    show(api, <ReviewQueueView />);
    expect(screen.queryByText(SUPPLIER)).toBeNull(); // the cached queue was dropped
    expect(screen.getByText("Loading the review queue…")).toBeTruthy();
    await refresh.release();
    expect(await screen.findByText("Nothing is waiting for review.")).toBeTruthy();
  });

  test("decision: lists, queue and audit trail are forgotten; the invoice is the one the API returned", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await screen.findByText("Extracted data");
    seedOtherViews();
    await approve();
    expectInvoiceViewsForgotten();
    const cached = cache.peek<{ invoice: { status: string } }>(keys.invoice(1))!.data;
    expect(cached.invoice.status).toBe("APPROVED_FOR_PAYMENT");
    expect(calls.getInvoice).toHaveBeenCalledTimes(1); // no extra request for what the action returned
  });

  test("correction: the same, and going back to the invoice shows the corrected data at once", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const first = show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await screen.findByText("Extracted data");
    seedOtherViews();
    await correctQuantity();
    expectInvoiceViewsForgotten();
    first.unmount();

    hold(calls.getInvoice);
    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    expect(screen.getByText(/Corrected by ana\.analyst/)).toBeTruthy();
    expect(screen.queryByText("Loading the invoice…")).toBeNull();
  });

  test("retry: everything about the invoice is forgotten and it is requested again", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const held = (await api.getInvoice(1)) as unknown as Record<string, unknown> & { invoice: object };
    const failed = {
      ...held,
      invoice: { ...held.invoice, status: "EXTRACTION_FAILED", status_reason: "The model service returned HTTP 503." },
      actions: { decisions: [], can_correct: false, can_retry: true },
      extraction: null,
      evaluation: null,
      extraction_versions: [],
    };
    calls.getInvoice.mockClear();
    calls.getInvoice.mockResolvedValueOnce(failed as never);
    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    const retry = await screen.findByRole("button", { name: "Retry extraction" });
    seedOtherViews();
    fireEvent.click(retry);
    await waitFor(() => expect(calls.retryExtraction).toHaveBeenCalledTimes(1));
    await screen.findByText("Extracted data"); // the invoice was requested again, once
    expect(calls.getInvoice).toHaveBeenCalledTimes(2);
    expectInvoiceViewsForgotten();
  });

  test("upload: other lists are forgotten and the list on screen is refreshed with one request", async () => {
    const { api, calls } = createFakeApi({ role: "INTAKE" });
    show(api, <InvoicesView session={session("INTAKE", "ines")} />);
    await screen.findByText(/Maximum size: 10 MB/);
    await waitFor(() => expect(calls.listInvoices).toHaveBeenCalledTimes(1));
    cache.set(keys.reviewQueue, ["old queue"]);
    cache.set(keys.invoiceList("HELD_FOR_REVIEW"), ["old filtered list"]);

    chooseFile("march.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await screen.findByText(/was received and is being processed/);
    expect(cache.peek(keys.reviewQueue)).toBeUndefined();
    expect(cache.peek(keys.invoiceList("HELD_FOR_REVIEW"))).toBeUndefined();
    expect(await screen.findByText("march.pdf", { selector: "td, td *" })).toBeTruthy();
    // The invalidation and the form's own refresh became a single request.
    expect(calls.listInvoices).toHaveBeenCalledTimes(2);
  });

  test("parameters: the new version is what is cached, and the history is requested again", async () => {
    const manager = session("AP_MANAGER", "max.manager");
    const { api, calls } = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    const first = show(api, <ParametersView session={manager} />);
    await screen.findByText("Parameters in force");
    await waitFor(() => expect(calls.parameterHistory).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.change(screen.getByLabelText("Comment (required)"), { target: { value: "A1 off." } });
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await screen.findByText(/Version 2 saved/);
    await waitFor(() => expect(calls.parameterHistory).toHaveBeenCalledTimes(2));
    expect(cache.peek<{ version: number }>(keys.parameters)!.data.version).toBe(2);
    expect(cache.peek<unknown[]>(keys.parameterHistory)!.data).toHaveLength(2);
    first.unmount();

    hold(calls.getParameters);
    show(api, <ParametersView session={manager} />);
    expect(screen.getByText("Parameters in force")).toBeTruthy();
    expect(screen.queryByText("Loading parameters…")).toBeNull();
    expect((screen.getAllByRole("checkbox")[0] as HTMLInputElement).checked).toBe(false); // version 2, not version 1
  });
});

describe("nothing crosses from one person to another", () => {
  test("after signing out, the next person does not see what the previous one loaded", async () => {
    devSignIn("ana.analyst", "AP_ANALYST");
    const reviewer = createFakeApi({ role: "AP_ANALYST" });
    const first = show(reviewer.api, <InvoicesView session={ANALYST} />);
    await screen.findByText(SUPPLIER);
    first.unmount();
    expect(cache.keys()).toContain("invoices:list:all");

    auth.signOut();
    expect(cache.keys()).toEqual([]);

    devSignIn("ines", "INTAKE");
    const intake = createFakeApi({ role: "INTAKE" });
    hold(intake.calls.listInvoices);
    show(intake.api, <InvoicesView session={session("INTAKE", "ines")} />); // the same cache key
    expect(screen.queryByText(SUPPLIER)).toBeNull();
    expect(screen.getByText("Loading documents…")).toBeTruthy();
  });

  test("switching role without signing out empties it as well", async () => {
    devSignIn("max.manager", "AP_MANAGER");
    const manager = createFakeApi({ role: "AP_MANAGER", username: "max.manager" });
    const first = show(manager.api, <InvoiceDetailView invoiceId={1} session={session("AP_MANAGER", "max.manager")} />);
    await screen.findByText("Audit trail");
    await waitFor(() => expect(cache.keys()).toContain("invoice:1:audit"));
    first.unmount();

    devSignIn("ana.analyst", "AP_ANALYST");
    expect(cache.keys()).toEqual([]);
  });
});

describe("the original document", () => {
  test("is requested each time the invoice is opened, released when it closes, and never cached", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    const first = show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await screen.findByTitle("Original document invoice.pdf");
    expect(calls.getDocument).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    first.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:document");

    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    expect(screen.getByText("Loading the document…")).toBeTruthy(); // the data is cached, the file is not
    await screen.findByTitle("Original document invoice.pdf");
    expect(calls.getDocument).toHaveBeenCalledTimes(2);

    for (const key of cache.keys()) {
      expect(key).toMatch(/^(invoice:1:detail|invoice:1:audit|invoices:|parameters:|me:upload)/);
      expect(cache.peek(key)!.data instanceof Blob).toBe(false);
    }
  });
});

describe("double requests in development", () => {
  test("under React Strict Mode the invoice is requested once; the uncached document, twice", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    render(
      <StrictMode>
        <Providers apiBaseUrl="/api" client={api}>
          <InvoiceDetailView invoiceId={1} session={ANALYST} />
        </Providers>
      </StrictMode>,
    );
    await screen.findByTitle("Original document invoice.pdf");
    expect(calls.getInvoice).toHaveBeenCalledTimes(1);
    // Strict Mode mounts, unmounts and mounts again in development: an effect that is
    // not deduplicated runs twice. The answer to the first run is ignored.
    expect(calls.getDocument).toHaveBeenCalledTimes(2);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  test("without Strict Mode, as in a production build, each is requested once", async () => {
    const { api, calls } = createFakeApi({ role: "AP_ANALYST" });
    show(api, <InvoiceDetailView invoiceId={1} session={ANALYST} />);
    await screen.findByTitle("Original document invoice.pdf");
    expect(calls.getInvoice).toHaveBeenCalledTimes(1);
    expect(calls.getDocument).toHaveBeenCalledTimes(1);
  });
});
