// The upload control: our own button and drop zone, in English, over the native file
// input, which stays in the page for keyboard and screen reader users.

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { InvoicesView } from "@/components/InvoicesView";
import { createFakeApi } from "./fake-api";
import { session, show } from "./screen-helpers";

async function open() {
  const fake = createFakeApi({ role: "INTAKE" });
  const view = show(fake.api, <InvoicesView session={session("INTAKE", "ines")} />);
  await screen.findByText(/Maximum size: 10 MB/);
  return { ...fake, ...view };
}

const pdf = (name = "invoice.pdf", bytes = 2411) =>
  new File([new Uint8Array(bytes)], name, { type: "application/pdf" });

describe("file picker", () => {
  test("shows its own texts, in English, instead of the browser's", async () => {
    const { container } = await open();
    expect(screen.getByText("Choose file")).toBeTruthy();
    expect(screen.getByText("or drop it here")).toBeTruthy();
    expect(screen.getByText("No file chosen.")).toBeTruthy();
    // The visible control is a label for the input, so clicking it opens the file dialog.
    const button = screen.getByText("Choose file");
    expect(button.tagName).toBe("LABEL");
    expect(button.getAttribute("for")).toBe("upload-file");
    expect(container.querySelector('input[type="file"]')!.id).toBe("upload-file");
  });

  test("the native input stays in the page: focusable, labelled, not removed from the accessibility tree", async () => {
    const { container } = await open();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.className).toBe("filepicker-input"); // visually hidden by CSS, not by an attribute
    expect(input.hidden).toBe(false);
    expect(input.disabled).toBe(false);
    expect(input.getAttribute("aria-hidden")).toBeNull();
    expect(input.getAttribute("tabindex")).toBeNull(); // in the normal tab order
    expect(input.style.display).toBe("");
    expect(screen.getByLabelText("Document")).toBe(input);
    expect(input.accept).toBe(".pdf,.png,.jpg,.jpeg");
    input.focus();
    expect(document.activeElement).toBe(input);
  });

  test("shows the name and size of the chosen file, and lets the person remove it", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Document"), { target: { files: [pdf("march-invoice.pdf", 2411)] } });
    expect(screen.getByText("march-invoice.pdf")).toBeTruthy();
    expect(screen.getByText("2.4 KB")).toBeTruthy();
    expect(screen.queryByText("No file chosen.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.queryByText("march-invoice.pdf")).toBeNull();
    expect(screen.getByText("No file chosen.")).toBeTruthy();
    expect((screen.getByLabelText("Document") as HTMLInputElement).value).toBe("");
  });

  test("a removed file is not uploaded", async () => {
    const { calls } = await open();
    fireEvent.change(screen.getByLabelText("Document"), { target: { files: [pdf()] } });
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("alert").textContent).toBe("Choose a file first.");
    expect(calls.uploadInvoice).not.toHaveBeenCalled();
  });

  test("a file can be dropped on the zone", async () => {
    const { calls, container } = await open();
    const zone = container.querySelector(".filepicker")!;
    fireEvent.dragOver(zone);
    expect(zone.className).toContain("filepicker-dragging");
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("dropped.pdf", 9 * 1024 * 1024)] } });
    expect(zone.className).not.toContain("filepicker-dragging");
    expect(screen.getByText("dropped.pdf")).toBeTruthy();
    expect(screen.getByText("9.0 MB")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(calls.uploadInvoice).toHaveBeenCalledTimes(1));
    expect((calls.uploadInvoice.mock.calls[0][0] as File).name).toBe("dropped.pdf");
  });

  test("the chosen file is the one uploaded, and the control resets afterwards", async () => {
    const { calls } = await open();
    fireEvent.change(screen.getByLabelText("Document"), { target: { files: [pdf("first.pdf")] } });
    fireEvent.change(screen.getByLabelText("Document"), { target: { files: [pdf("second.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await screen.findByText(/was received and is being processed/);
    expect((calls.uploadInvoice.mock.calls[0][0] as File).name).toBe("second.pdf");
    expect(screen.getByText("No file chosen.")).toBeTruthy();
  });

  test("limits still apply to a dropped file", async () => {
    const { calls, container } = await open();
    fireEvent.drop(container.querySelector(".filepicker")!, {
      dataTransfer: { files: [new File(["x"], "notes.docx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByRole("alert").textContent).toContain("is not a supported file type");
    expect(calls.uploadInvoice).not.toHaveBeenCalled();
  });
});
