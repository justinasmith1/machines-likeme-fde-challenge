"use client";

import type { Evaluation, Reason } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { RULE_SOURCE_LABELS, SUPPLIER_METHOD_LABELS } from "@/lib/invoices/actions";
import { StatusBadge } from "../Feedback";

export function ReasonsTable({ reasons, caption }: { reasons: Reason[]; caption: string }) {
  return (
    <table>
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Rule</th>
          <th scope="col">Reason</th>
          <th scope="col">Effect</th>
          <th scope="col">Origin</th>
        </tr>
      </thead>
      <tbody>
        {reasons.map((reason, index) => (
          <tr key={index}>
            <td>
              <code>{reason.code}</code>
            </td>
            <td>{reason.message}</td>
            <td>
              <StatusBadge status={reason.outcome} />
            </td>
            <td>
              {RULE_SOURCE_LABELS[reason.source] ?? reason.source}
              {reason.decision_log_ref ? ` (${reason.decision_log_ref})` : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Shown when the engine would pay but a correction keeps the invoice held (DL-46). */
export const HELD_AFTER_CORRECTION_NOTE = "After a correction the invoice stays held until a person decides (DL-46).";

export function EvaluationPanel({
  evaluation,
  heldAfterCorrection = false,
}: {
  evaluation: Evaluation | null;
  /** The invoice is held because its data was corrected, whatever the engine's result. */
  heldAfterCorrection?: boolean;
}) {
  if (!evaluation) {
    return (
      <section className="panel" aria-labelledby="evaluation-title">
        <h2 id="evaluation-title">Rules evaluation</h2>
        <p className="state state-empty">The rules engine has not evaluated this document.</p>
      </section>
    );
  }
  const { matching, reasons } = evaluation;
  const parametersVersion = evaluation.parameters_snapshot.version;
  return (
    <section className="panel" aria-labelledby="evaluation-title">
      <h2 id="evaluation-title">Rules evaluation</h2>
      <p>
        Engine outcome: <StatusBadge status={evaluation.outcome} />{" "}
        <span className="muted">
          evaluated {formatDateTime(evaluation.evaluated_at)}
          {parametersVersion !== undefined ? `, parameters version ${String(parametersVersion)}` : ""}
        </span>
      </p>
      {heldAfterCorrection && evaluation.outcome === "AUTO_PAY" && (
        <p className="hint hint-warning">
          <span aria-hidden="true">⚠</span> {HELD_AFTER_CORRECTION_NOTE}
        </p>
      )}
      <p className="hint">
        Supplier: {matching.supplier_id ?? "none"} (
        {SUPPLIER_METHOD_LABELS[matching.supplier_method ?? ""] ?? matching.supplier_method}). Purchase orders:{" "}
        {matching.po_references.length > 0 ? matching.po_references.join(", ") : "none"}
        {matching.missing_po_numbers.length > 0 ? `. Not found: ${matching.missing_po_numbers.join(", ")}` : ""}.
      </p>
      {reasons.length === 0 ? (
        <p className="state state-empty">No rule fired. Every check passed.</p>
      ) : (
        <ReasonsTable reasons={reasons} caption="Every rule that fired for this invoice" />
      )}
    </section>
  );
}
