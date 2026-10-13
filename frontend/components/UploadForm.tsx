"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import type { Me, UploadReceipt } from "@/lib/api/types";
import { extensionsLabel, fileSizeLabel, formatFileSize } from "@/lib/format";
import { invoicesChanged, keys, useLoad } from "@/lib/hooks";
import { ErrorNotice, Notice } from "./Feedback";
import { useApi } from "./Providers";

/** Checked before sending, so the person finds out at once. The API checks again. */
export function uploadProblem(file: File, limits: Me["upload"]): string | null {
  const extension = file.name.includes(".") ? `.${file.name.split(".").pop()!.toLowerCase()}` : "";
  if (!limits.extensions.includes(extension)) {
    return `"${file.name}" is not a supported file type. Upload ${extensionsLabel(limits.extensions)}.`;
  }
  if (file.size > limits.max_mb * 1024 * 1024) {
    return `"${file.name}" is larger than the ${fileSizeLabel(limits.max_mb)} limit.`;
  }
  if (file.size === 0) return `"${file.name}" is empty.`;
  return null;
}

export function UploadForm({ onUploaded }: { onUploaded: (receipt: UploadReceipt) => void }) {
  const api = useApi();
  const limits = useLoad(keys.uploadLimits, () => api.me().then((me) => me.upload));
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [sending, setSending] = useState(false);
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null);

  function choose(chosen: File | null) {
    setFile(chosen);
    setProblem(null);
    setError(undefined);
    if (chosen) setReceipt(null);
  }

  function remove() {
    choose(null);
    if (input.current) input.current.value = "";
  }

  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer?.files?.[0];
    if (dropped) choose(dropped);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setReceipt(null);
    if (!file) {
      setProblem("Choose a file first.");
      return;
    }
    const found = limits.data ? uploadProblem(file, limits.data) : null;
    setProblem(found);
    if (found) return;

    setSending(true);
    try {
      const uploaded = await api.uploadInvoice(file);
      invoicesChanged();
      setFile(null);
      if (input.current) input.current.value = "";
      setReceipt(uploaded);
      onUploaded(uploaded);
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} aria-labelledby="upload-title">
      <h2 id="upload-title">Upload a document</h2>
      <p className="hint">
        {limits.data
          ? `Accepted: ${extensionsLabel(limits.data.extensions)}. Maximum size: ${fileSizeLabel(limits.data.max_mb)}. One document per upload.`
          : "Accepted: PDF or image. One document per upload."}
      </p>

      {/*
        The browser's own file control shows its texts in the system language, so it is
        visually hidden and driven through a label styled as a button. It stays in the page:
        keyboard focus, Enter / Space and screen readers work as with any file input.
      */}
      <div
        className={dragging ? "filepicker filepicker-dragging" : "filepicker"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
      >
        <label htmlFor="upload-file" className="sr-only">
          Document
        </label>
        <input
          id="upload-file"
          ref={input}
          className="filepicker-input"
          type="file"
          accept={limits.data?.extensions.join(",")}
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
        />
        <label htmlFor="upload-file" className="button secondary filepicker-button">
          Choose file
        </label>
        <span className="filepicker-hint">or drop it here</span>
      </div>

      <div className="filepicker-chosen" aria-live="polite">
        {file ? (
          <>
            <span className="filepicker-name">{file.name}</span>
            <span className="filepicker-size">{formatFileSize(file.size)}</span>
            <button type="button" className="link" onClick={remove} disabled={sending}>
              Remove
            </button>
          </>
        ) : (
          <span className="muted">No file chosen.</span>
        )}
      </div>

      <button type="submit" disabled={sending}>
        {sending ? "Uploading…" : "Upload"}
      </button>

      {problem && (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
      {receipt && (
        <Notice tone="success">
          <strong>{receipt.filename}</strong> was received and is being processed. Its status appears in the
          list below and updates by itself.
        </Notice>
      )}
    </form>
  );
}
