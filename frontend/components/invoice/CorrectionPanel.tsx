"use client";

import { useMemo, useState, type FormEvent } from "react";
import type { Extraction, ReviewerInvoice } from "@/lib/api/types";
import {
  DOCUMENT_TYPES,
  HEADER_FIELDS,
  LINE_FIELDS,
  buildCorrections,
  toCorrectionForm,
  validateCorrectionForm,
  type CorrectionForm,
} from "@/lib/invoices/corrections";
import { ErrorNotice } from "../Feedback";
import { useApi } from "../Providers";

interface CorrectionFormProps {
  invoiceId: number;
  extraction: Extraction;
  onCorrected: (detail: ReviewerInvoice) => void;
  onClose: () => void;
}

/** Correcting data and approving payment are two separate actions (DL-46). */
export const CORRECTION_DOES_NOT_APPROVE =
  "Saving a correction does not approve payment: the invoice stays held until someone records a decision";

/**
 * The form to correct what was read, shown inside the "Extracted data" panel. Saving a
 * correction never pays: the form says so before saving, and the page says so after.
 */
export function CorrectionForm({ invoiceId, extraction, onCorrected, onClose }: CorrectionFormProps) {
  const api = useApi();
  const original = useMemo(() => toCorrectionForm(extraction), [extraction]);
  const [form, setForm] = useState<CorrectionForm>(original);
  const [comment, setComment] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(undefined);
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    const found = validateCorrectionForm(form);
    const corrections = buildCorrections(original, form);
    if (!corrections) found.push("Nothing was changed.");
    if (!comment.trim()) found.push("A comment explaining the correction is required.");
    setProblems(found);
    if (found.length > 0 || !corrections) return;

    setSending(true);
    try {
      const detail = await api.correctExtraction(invoiceId, corrections, comment.trim());
      onClose();
      onCorrected(detail);
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="correction" role="group" aria-labelledby="correction-title">
      <h3 id="correction-title">Correct the extracted data</h3>
      <p className="hint hint-warning">
        <span aria-hidden="true">⚠</span> {CORRECTION_DOES_NOT_APPROVE} (DL-46).
      </p>
      <p className="hint">
        Change only what is wrong. Leave a field empty if it is not on the document. Suggestions are not filled
        in for you: type the value only if you can read it on the document.
      </p>
      <form onSubmit={submit}>
        <label htmlFor="correction-type">Document type</label>
        <select
          id="correction-type"
          value={form.document_type}
          onChange={(event) => setForm({ ...form, document_type: event.target.value })}
        >
          {DOCUMENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>

        <div className="formgrid">
          {HEADER_FIELDS.map(({ name, label }) => (
            <div key={name}>
              <label htmlFor={`correction-${name}`}>{label}</label>
              <input
                id={`correction-${name}`}
                value={form.header[name]}
                onChange={(event) =>
                  setForm({ ...form, header: { ...form.header, [name]: event.target.value } })
                }
              />
            </div>
          ))}
        </div>

        {form.lines.map((line, index) => (
          <fieldset key={index}>
            <legend>Line {index + 1}</legend>
            <div className="formgrid">
              {LINE_FIELDS.map(({ name, label }) => (
                <div key={name}>
                  <label htmlFor={`correction-line-${index}-${name}`}>{label}</label>
                  <input
                    id={`correction-line-${index}-${name}`}
                    value={line[name]}
                    onChange={(event) => {
                      const lines = form.lines.map((item, position) =>
                        position === index ? { ...item, [name]: event.target.value } : item,
                      );
                      setForm({ ...form, lines });
                    }}
                  />
                </div>
              ))}
            </div>
          </fieldset>
        ))}

        <label htmlFor="correction-comment">Comment (required)</label>
        <textarea
          id="correction-comment"
          rows={2}
          value={comment}
          onChange={(event) => setComment(event.target.value)}
        />
        <div className="row">
          <button type="submit" disabled={sending}>
            {sending ? "Saving…" : "Save correction"}
          </button>
          <button type="button" className="secondary" onClick={onClose} disabled={sending}>
            Cancel
          </button>
        </div>
      </form>
      {problems.length > 0 && (
        <div className="notice notice-error" role="alert">
          <ul className="plain">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
    </div>
  );
}
