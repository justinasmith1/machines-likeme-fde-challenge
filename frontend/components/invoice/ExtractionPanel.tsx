"use client";

import { useState } from "react";
import type { Extraction, FieldOrigin, PoReferences, ReadField, ReadStatus, ReviewerInvoice } from "@/lib/api/types";
import { formatAmount } from "@/lib/format";
import { MAPPING_METHOD_LABELS } from "@/lib/invoices/actions";
import { describeOrigin } from "@/lib/invoices/origin";
import { CORRECTION_DOES_NOT_APPROVE, CorrectionForm } from "./CorrectionPanel";

/**
 * Where the value comes from. A value typed by a person is tagged "Corrected by <user>"
 * with what the field was before, never "Read": only the model's readings are "Read".
 */
function OriginTag({ origin, status }: { origin?: FieldOrigin | null; status?: ReadStatus | null }) {
  const text = describeOrigin(origin, status);
  if (text.corrected) {
    return (
      <span className="tag tag-corrected">
        <span aria-hidden="true">✎</span> {text.label} <span className="was">({text.was})</span>
      </span>
    );
  }
  if (!status) return null;
  return (
    <span className={`tag tag-${status}`}>
      <span aria-hidden="true">{status === "read" ? "✓" : status === "illegible" ? "?" : "–"}</span> {text.label}
    </span>
  );
}

/**
 * A suggestion is never shown as the value (DL-18). It gets its own box, a "Suggestion"
 * label and a dashed border, and says the rules engine does not use it.
 */
function SuggestionBox({ text, note }: { text: string; note: string }) {
  return (
    <span className="suggestion" role="note">
      <span className="suggestion-label">Suggestion</span> {text}
      <span className="suggestion-note">{note}</span>
    </span>
  );
}

/** A field with its reading status or correction. The value cell is empty when nothing was read. */
export function ReadFieldView({ field, amount = false }: { field: ReadField; amount?: boolean }) {
  return (
    <span className="readfield">
      <span className={field.value === null ? "value value-missing" : "value"}>
        {field.value === null ? "No value read" : amount ? formatAmount(field.value) : field.value}
      </span>{" "}
      <OriginTag origin={field.origin} status={field.status} />
      {field.suggestion && <SuggestionBox text={field.suggestion.value} note={field.suggestion.note} />}
    </span>
  );
}

function PoReferencesView({ field }: { field: PoReferences }) {
  return (
    <span className="readfield">
      <span className={field.values.length === 0 ? "value value-missing" : "value"}>
        {field.values.length === 0 ? "No value read" : field.values.join(", ")}
      </span>{" "}
      <OriginTag origin={field.origin} status={field.status} />
      {field.suggestion && (
        <SuggestionBox text={field.suggestion.values.join(", ")} note={field.suggestion.note} />
      )}
    </span>
  );
}

/** A field without a reading status. It is tagged only when a person corrected it. */
function PlainValue({ value, origin }: { value: string; origin?: FieldOrigin }) {
  return (
    <span className="readfield">
      <span className="value">{value}</span> {origin && <OriginTag origin={origin} />}
    </span>
  );
}

interface ExtractionPanelProps {
  extraction: Extraction;
  /** Given only when the API allows this person to correct the data. */
  correction?: { invoiceId: number; onCorrected: (detail: ReviewerInvoice) => void };
}

export function ExtractionPanel({ extraction, correction }: ExtractionPanelProps) {
  const [correcting, setCorrecting] = useState(false);
  const { header } = extraction;
  if (!header) {
    return <p className="state state-empty">This extraction is stored in a format that cannot be displayed.</p>;
  }
  const corrected = extraction.corrected_fields ?? {};
  return (
    <section className="panel" aria-labelledby="extraction-title">
      <h2 id="extraction-title">Extracted data</h2>
      <p className="hint">
        Version {extraction.version},{" "}
        {extraction.source === "MODEL" ? "read by the model" : `corrected by ${extraction.created_by}`}. Document
        type: <strong>{extraction.document_type}</strong>
        {extraction.classification_reason ? ` (${extraction.classification_reason})` : ""}
        {corrected.document_type && (
          <>
            {" "}
            <OriginTag origin={corrected.document_type} />
          </>
        )}
      </p>
      <dl className="fields">
        <dt>Supplier</dt>
        <dd>
          <PlainValue value={header.supplier_name ?? "–"} origin={corrected.supplier_name} />
        </dd>
        <dt>Supplier tax ID</dt>
        <dd>
          <PlainValue value={header.supplier_tax_id ?? "–"} origin={corrected.supplier_tax_id} />
        </dd>
        <dt>Invoice number</dt>
        <dd>
          <ReadFieldView field={header.invoice_number} />
        </dd>
        <dt>Invoice date</dt>
        <dd>
          <ReadFieldView field={header.invoice_date} />
        </dd>
        <dt>Due date</dt>
        <dd>
          <PlainValue value={header.due_date ?? "–"} origin={corrected.due_date} />
        </dd>
        <dt>Purchase orders</dt>
        <dd>
          <PoReferencesView field={header.po_references} />
        </dd>
        <dt>Currency</dt>
        <dd>
          <PlainValue value={header.currency ?? "–"} origin={corrected.currency} />
        </dd>
        <dt>Subtotal</dt>
        <dd className="amount">
          <PlainValue value={formatAmount(header.subtotal)} origin={corrected.subtotal} />
        </dd>
        <dt>Tax</dt>
        <dd className="amount">
          <PlainValue value={formatAmount(header.tax_amount)} origin={corrected.tax_amount} />
        </dd>
        <dt>Total</dt>
        <dd className="amount">
          <PlainValue value={formatAmount(header.total_amount)} origin={corrected.total_amount} />
        </dd>
      </dl>
      {correction &&
        (correcting ? (
          <CorrectionForm
            invoiceId={correction.invoiceId}
            extraction={extraction}
            onCorrected={correction.onCorrected}
            onClose={() => setCorrecting(false)}
          />
        ) : (
          <div className="panel-actions">
            <button type="button" className="secondary" onClick={() => setCorrecting(true)}>
              Correct data
            </button>
            <span className="hint">
              For when the document says something different from what was read. {CORRECTION_DOES_NOT_APPROVE}.
            </span>
          </div>
        ))}

    </section>
  );
}

/**
 * The invoice lines, each with the purchase order line it was matched to. Its own panel,
 * across the whole page: six columns do not fit next to the document.
 */
export function LinesPanel({ extraction }: { extraction: Extraction }) {
  const { lines } = extraction;
  return (
    <section className="panel" aria-labelledby="lines-title">
      <h2 id="lines-title">Invoice lines</h2>
      {!lines || lines.length === 0 ? (
        <p className="state state-empty">No lines were read from the document.</p>
      ) : (
        <table>
          <caption className="sr-only">Invoice lines and the purchase order line each one was matched to</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Description</th>
              <th scope="col">Quantity</th>
              <th scope="col">Unit price</th>
              <th scope="col" className="num">
                Line total
              </th>
              <th scope="col">Matched PO line</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => {
              const fixed = line.corrected_fields ?? {};
              return (
                <tr key={line.line_number}>
                  <td className="num">{line.line_number}</td>
                  <td>
                    <PlainValue value={line.description ?? "–"} origin={fixed.description} />
                  </td>
                  <td>
                    <ReadFieldView field={line.quantity} />
                  </td>
                  <td>
                    <ReadFieldView field={line.unit_price} amount />
                  </td>
                  <td className="num">
                    <PlainValue value={formatAmount(line.line_total)} origin={fixed.line_total} />
                  </td>
                  <td>
                    {line.po_line_mapping?.po_number ? (
                      <>
                        {line.po_line_mapping.po_number}, line {line.po_line_mapping.po_line_number}
                      </>
                    ) : (
                      <span className="value-missing">Not matched</span>
                    )}
                    {line.po_line_mapping && (
                      <span className="muted block">
                        {MAPPING_METHOD_LABELS[line.po_line_mapping.method] ?? line.po_line_mapping.method}
                      </span>
                    )}
                    {fixed.po_line_reference && (
                      <span className="block">
                        <OriginTag origin={fixed.po_line_reference} />
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
