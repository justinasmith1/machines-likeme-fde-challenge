// Invoice detail, as laid out after review in the browser: the lines across the whole
// page, the document column that stays in view, the note when a correction keeps the
// invoice held, and a navigation that follows where the person came from.

import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import InvoiceDetailPage from "@/app/invoices/[id]/page";
import { InvoicesView } from "@/components/InvoicesView";
import { ReviewQueueView } from "@/components/ReviewQueueView";
import { HELD_AFTER_CORRECTION_NOTE } from "@/components/invoice/EvaluationPanel";
import { InvoiceDetailView } from "@/components/invoice/InvoiceDetailView";
import type { ReviewerInvoice } from "@/lib/api/types";
import { devSignIn } from "@/lib/auth/dev";
import { invoiceHref, originFromQuery } from "@/lib/invoices/actions";
import { createFakeApi } from "./fake-api";
import { location } from "./navigation-mock";
import { session, show } from "./screen-helpers";

const LINES = "Invoice lines and the purchase order line each one was matched to";

async function openDetail() {
  const fake = createFakeApi({ role: "AP_ANALYST" });
  const view = show(fake.api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
  await screen.findByText("Extracted data");
  return { ...fake, ...view };
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

describe("layout of the invoice detail", () => {
  test("the lines have their own panel, outside the two columns and right after them", async () => {
    const { container } = await openDetail();
    const split = container.querySelector(".split")!;
    const table = screen.getByRole("table", { name: LINES });
    expect(split.contains(table)).toBe(false);

    const panel = table.closest("section")!;
    expect(panel.className).toBe("panel");
    expect(within(panel).getByRole("heading", { name: "Invoice lines" })).toBeTruthy();
    expect(split.nextElementSibling).toBe(panel);
    // The header fields stay next to the document, without the table.
    const extracted = screen.getByRole("heading", { name: "Extracted data" }).closest("section")!;
    expect(split.contains(extracted)).toBe(true);
    expect(extracted.querySelector("table")).toBeNull();
  });

  test("the left column holds only the document and is the one that sticks", async () => {
    const { container } = await openDetail();
    const [left, right] = Array.from(container.querySelector(".split")!.children);
    expect(left.className).toBe("split-document");
    expect(left.querySelector("section")).toBeNull();
    // Beside the document: what was read from it and what the rules made of it. Nothing else.
    const beside = Array.from(right.querySelectorAll("section > h2")).map((heading) => heading.textContent);
    expect(beside).toEqual(["Extracted data", "Rules evaluation"]);
  });

  test("the page reads in order: columns, lines, decision, then one history card", async () => {
    const { container } = await openDetail();
    const split = container.querySelector(".split")!;
    const lines = split.nextElementSibling!;
    const decision = lines.nextElementSibling!;
    expect(within(lines as HTMLElement).getByRole("heading", { name: "Invoice lines" })).toBeTruthy();
    expect(within(decision as HTMLElement).getByRole("heading", { name: "Decision" })).toBeTruthy();
    // Full width, and still the main action of the page.
    expect(split.contains(decision)).toBe(false);
    expect(decision.className).toContain("panel-primary");

    const after = [];
    for (let next = decision.nextElementSibling; next; next = next.nextElementSibling) {
      after.push(next.querySelector("h2")?.textContent);
    }
    expect(after).toEqual(["History"]);
  });
});

describe("correcting from the 'Extracted data' panel", () => {
  const extracted = () => screen.getByRole("heading", { name: "Extracted data" }).closest("section")!;

  test("'Correct data' is a secondary button inside the panel, and says it does not approve payment", async () => {
    await openDetail();
    const button = within(extracted()).getByRole("button", { name: "Correct data" });
    expect(button.className).toBe("secondary");
    expect(screen.getAllByRole("button", { name: "Correct data" })).toHaveLength(1);
    expect(button.parentElement!.textContent).toContain(
      "Saving a correction does not approve payment: the invoice stays held until someone records a decision.",
    );
    // No separate correction panel any more.
    expect(screen.queryByRole("heading", { name: "Correct the extracted data" })).toBeNull();
  });

  test("the form opens in the same panel and shows the DL-46 text before anything is saved", async () => {
    const { calls } = await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Correct data" }));

    const form = within(extracted()).getByRole("group", { name: "Correct the extracted data" });
    const warning = form.querySelector(".hint-warning")!;
    expect(warning.textContent).toContain(
      "Saving a correction does not approve payment: the invoice stays held until someone records a decision (DL-46).",
    );
    expect(warning.textContent).toContain("⚠"); // not only a colour
    // It comes before the fields and the save button.
    const save = within(form).getByRole("button", { name: "Save correction" });
    expect(warning.compareDocumentPosition(save) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(warning.compareDocumentPosition(form.querySelector("form")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(calls.correctExtraction).not.toHaveBeenCalled();
    // What was read stays visible above the form, and the button is replaced by the form.
    expect(extracted().querySelector("dl.fields")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Correct data" })).toBeNull();
  });

  test("cancel closes the form without saving and brings the button back", async () => {
    const { calls } = await openDetail();
    fireEvent.click(screen.getByRole("button", { name: "Correct data" }));
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Correct the extracted data" })).toBeNull();
    expect(calls.correctExtraction).not.toHaveBeenCalled();
    // Reopened, the form starts from what was read, not from what was typed and discarded.
    fireEvent.click(screen.getByRole("button", { name: "Correct data" }));
    expect((screen.getByLabelText("Quantity") as HTMLInputElement).value).toBe("");
  });

  test("after saving, the form closes and the notice says the invoice is still held", async () => {
    await openDetail();
    await correctQuantity();
    expect(screen.queryByRole("group", { name: "Correct the extracted data" })).toBeNull();
    const notice = screen.getByText("Correction saved.").closest(".notice")!;
    expect(notice.textContent).toContain("Saving a correction does not approve payment");
    expect(notice.textContent).toContain("stays held until someone records a decision below");
    expect(within(extracted()).getByRole("button", { name: "Correct data" })).toBeTruthy();
  });

  test("a person the API does not let correct gets no button and no form", async () => {
    const fake = createFakeApi({ role: "AP_ANALYST" });
    const detail = (await fake.api.getInvoice(1)) as ReviewerInvoice;
    fake.calls.getInvoice.mockResolvedValue({ ...detail, actions: { ...detail.actions, can_correct: false } });
    show(fake.api, <InvoiceDetailView invoiceId={1} session={session("AP_ANALYST")} />);
    await screen.findByText("Extracted data");
    expect(screen.queryByRole("button", { name: "Correct data" })).toBeNull();
    expect(screen.queryByText(/Saving a correction does not approve payment/)).toBeNull();
    // The decision is unaffected.
    expect(screen.getByRole("heading", { name: "Decision" })).toBeTruthy();
  });
});

describe("history card", () => {
  const part = (title: string) => screen.queryByText(title, { selector: ".history-title" })?.closest("details") ?? null;
  const summaryOf = (title: string) => part(title)!.querySelector("summary")!.textContent;

  test("a fresh invoice shows only the extraction versions: no empty parts", async () => {
    await openDetail();
    const card = screen.getByRole("heading", { name: "History" }).closest("section")!;
    expect(card.querySelectorAll("details")).toHaveLength(1);
    expect(part("Decisions")).toBeNull();
    expect(part("Earlier evaluations")).toBeNull();
    expect(summaryOf("Extraction versions")).toBe("Extraction versions (1)");
    expect(screen.queryByText("Nobody has decided on this invoice yet.")).toBeNull();
    expect(screen.queryByText(/This invoice has been evaluated once/)).toBeNull();
    // The old separate cards are gone.
    for (const old of ["Decision history", "Earlier evaluations", "Extraction versions"]) {
      expect(screen.queryByRole("heading", { name: old })).toBeNull();
    }
  });

  test("each part says how many items it has, in one card, in a fixed order", async () => {
    await openDetail();
    await correctQuantity();
    fireEvent.click(screen.getByLabelText("Approve for payment"));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Checked against the paper copy." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");

    const card = screen.getByRole("heading", { name: "History" }).closest("section")!;
    const summaries = Array.from(card.querySelectorAll("details > summary")).map((summary) => summary.textContent);
    expect(summaries).toEqual(["Decisions (1)", "Earlier evaluations (1)", "Extraction versions (2)"]);
    expect(part("Decisions")!.querySelectorAll(".timeline > li")).toHaveLength(1);
    expect(part("Extraction versions")!.querySelectorAll(".timeline > li")).toHaveLength(2);
  });

  test("the decisions start open; the other parts start folded and can be opened", async () => {
    await openDetail();
    await correctQuantity();
    fireEvent.click(screen.getByLabelText("Approve for payment"));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Checked." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");

    expect(part("Decisions")!.open).toBe(true);
    expect(part("Earlier evaluations")!.open).toBe(false);
    expect(part("Extraction versions")!.open).toBe(false);
    // Native <details>: keyboard and screen readers get folding for free.
    expect(part("Earlier evaluations")!.tagName).toBe("DETAILS");
    expect(part("Earlier evaluations")!.firstElementChild!.tagName).toBe("SUMMARY");
  });

  test("with no decision, nothing starts open", async () => {
    await openDetail();
    await correctQuantity();
    expect(part("Decisions")).toBeNull();
    expect(part("Earlier evaluations")!.open).toBe(false);
    expect(part("Extraction versions")!.open).toBe(false);
  });
});

describe("engine says Auto-Pay but a correction keeps the invoice held (DL-46)", () => {
  test("no note while the engine itself holds the invoice", async () => {
    await openDetail();
    expect(screen.queryByText(HELD_AFTER_CORRECTION_NOTE, { exact: false })).toBeNull();
  });

  test("after a correction, the note sits under the engine outcome", async () => {
    await openDetail();
    await correctQuantity();
    const evaluation = screen.getByRole("heading", { name: "Rules evaluation" }).closest("section")!;
    const outcome = within(evaluation).getByText(/Engine outcome:/);
    expect(outcome.textContent).toContain("Auto-Pay");
    const note = outcome.nextElementSibling!;
    expect(note.textContent).toContain("After a correction the invoice stays held until a person decides (DL-46).");
    expect(note.className).toContain("hint-warning");
    expect(note.textContent).toContain("⚠"); // not only a colour
    // The invoice is still held, and the decision is still to be made.
    expect(within(screen.getByRole("region", { name: "Invoice summary" })).getByText(/Held for review/)).toBeTruthy();
  });

  test("the note goes away once a person has decided", async () => {
    await openDetail();
    await correctQuantity();
    fireEvent.click(screen.getByLabelText("Approve for payment"));
    fireEvent.change(screen.getByLabelText("Comment (required)", { selector: "#decision-comment" }), {
      target: { value: "Checked against the paper copy." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded: Approve for payment.");
    expect(screen.queryByText(HELD_AFTER_CORRECTION_NOTE, { exact: false })).toBeNull();
  });
});

describe("navigation on the invoice page follows where the person came from", () => {
  async function openPage(search: string) {
    location.pathname = "/invoices/1";
    location.search = search;
    devSignIn("ana.analyst", "AP_ANALYST");
    show(createFakeApi({ role: "AP_ANALYST" }).api, <InvoiceDetailPage />);
    await screen.findByText("Extracted data");
    const links = within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("link");
    return Object.fromEntries(links.map((link) => [link.textContent, link.getAttribute("aria-current")]));
  }

  test("from the review queue: 'Review queue' is the current item, not 'Invoices'", async () => {
    expect(await openPage("?from=review")).toEqual({ "Review queue": "page", Invoices: null, Parameters: null });
    expect(screen.getByRole("link", { name: "← Review queue" }).getAttribute("href")).toBe("/review");
  });

  test("from the list of invoices: 'Invoices' is the current item, and the link back goes there", async () => {
    expect(await openPage("?from=invoices")).toEqual({ "Review queue": null, Invoices: "page", Parameters: null });
    expect(screen.getByRole("link", { name: "← Invoices" }).getAttribute("href")).toBe("/invoices");
  });

  test.each(["", "?from=somewhere-else"])("opened directly (%s): no item is marked", async (search) => {
    expect(await openPage(search)).toEqual({ "Review queue": null, Invoices: null, Parameters: null });
    expect(screen.getByRole("link", { name: "← Review queue" })).toBeTruthy();
  });

  test("the queue and the list say where their links come from", async () => {
    const queue = show(createFakeApi({ role: "AP_ANALYST" }).api, <ReviewQueueView />);
    const fromQueue = (await screen.findAllByRole("link")).map((link) => link.getAttribute("href"));
    expect(fromQueue).toContain("/invoices/1?from=review");
    queue.unmount();

    show(createFakeApi({ role: "AP_ANALYST" }).api, <InvoicesView session={session("AP_ANALYST")} />);
    const table = await screen.findByRole("table", { name: "All invoices" });
    const fromList = within(table).getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(fromList.length).toBeGreaterThan(0);
    expect(fromList.every((href) => /^\/invoices\/\d+\?from=invoices$/.test(href!))).toBe(true);
  });

  test("only our two lists are accepted as an origin", () => {
    expect(invoiceHref(7, "review")).toBe("/invoices/7?from=review");
    expect(originFromQuery("review")).toEqual({ href: "/review", label: "Review queue" });
    expect(originFromQuery("invoices")).toEqual({ href: "/invoices", label: "Invoices" });
    for (const other of [null, undefined, "", "parameters", "//elsewhere.example"]) {
      expect(originFromQuery(other)).toBeNull();
    }
  });
});
