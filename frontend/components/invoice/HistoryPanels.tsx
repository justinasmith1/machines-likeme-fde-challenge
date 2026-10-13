"use client";

import type { ReactNode } from "react";
import type { AuditEvent, DecisionRecord, Evaluation, ExtractionVersion } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/invoices/actions";
import { describeAuditEvent } from "@/lib/invoices/audit";
import { keys, useLoad } from "@/lib/hooks";
import { ErrorNotice, Loading, StatusBadge } from "../Feedback";
import { useApi } from "../Providers";
import { ReasonsTable } from "./EvaluationPanel";

const DECISION_LABELS: Record<string, string> = {
  APPROVE: "Approved for payment",
  OVERRIDE: "Overrode the rejection and approved",
  REJECT: "Rejected",
  REQUEST_CREDIT_NOTE: "Requested a credit note",
};

const SUPERSEDED_LABELS: Record<string, string> = {
  NEW_DECISION: "replaced by a later decision",
  EXTRACTION_CORRECTED: "no longer applies because the data was corrected",
};

const roleLabel = (role: string) => ROLE_LABELS[role as keyof typeof ROLE_LABELS] ?? role;

/** One part of the history card: a title with how many items it has, folded or not. */
function HistoryPart({ title, count, open = false, children }: { title: string; count: number; open?: boolean; children: ReactNode }) {
  return (
    <details className="history-part" open={open}>
      <summary>
        <span className="history-title">{title}</span> <span className="history-count">({count})</span>
      </summary>
      {children}
    </details>
  );
}

function DecisionList({ decisions }: { decisions: DecisionRecord[] }) {
  return (
    <ol className="timeline">
      {[...decisions].reverse().map((decision) => (
        <li key={decision.id} className={decision.superseded ? "superseded" : undefined}>
          <strong>{DECISION_LABELS[decision.decision] ?? decision.decision}</strong> by {decision.decided_by} (
          {roleLabel(decision.role)}), {formatDateTime(decision.decided_at)}
          {decision.superseded ? (
            <span className="tag tag-absent">
              Superseded: {SUPERSEDED_LABELS[decision.superseded_reason ?? ""] ?? "no longer in force"}
            </span>
          ) : (
            <span className="tag tag-read">In force</span>
          )}
          <blockquote>{decision.comment}</blockquote>
          <span className="muted">
            Status before: <StatusBadge status={decision.previous_status} />. Role required:{" "}
            {roleLabel(decision.required_role)}.
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * Earlier runs of the rules engine, newest first. After a correction the evaluation in
 * force may show no reasons at all; this keeps why the invoice was held or rejected before.
 */
function EvaluationList({ evaluations }: { evaluations: Evaluation[] }) {
  return (
    <ol className="timeline">
      {evaluations.map((evaluation) => (
        <li key={evaluation.id}>
          <strong>Evaluation of extraction version {evaluation.extraction_version ?? "–"}</strong>{" "}
          <StatusBadge status={evaluation.outcome} />
          <span className="muted block">
            {formatDateTime(evaluation.evaluated_at)}
            {evaluation.parameters_snapshot.version !== undefined
              ? ` · parameters version ${String(evaluation.parameters_snapshot.version)}`
              : ""}
          </span>
          {evaluation.reasons.length === 0 ? (
            <p className="state state-empty">No rule fired.</p>
          ) : (
            <ReasonsTable
              reasons={evaluation.reasons}
              caption={`Rules that fired in the evaluation of extraction version ${evaluation.extraction_version ?? ""}`}
            />
          )}
        </li>
      ))}
    </ol>
  );
}

function VersionList({ versions }: { versions: ExtractionVersion[] }) {
  return (
    <ol className="timeline">
      {[...versions].reverse().map((version) => (
        <li key={version.version}>
          <strong>Version {version.version}</strong>:{" "}
          {/* The deployment name is technical detail: it stays in the audit trail. */}
          {version.source === "MODEL" ? "read by the model" : `corrected by ${version.created_by}`}
          , {formatDateTime(version.created_at)}
          {version.comment && <blockquote>{version.comment}</blockquote>}
        </li>
      ))}
    </ol>
  );
}

interface HistoryCardProps {
  decisions: DecisionRecord[];
  /** Every evaluation, oldest first. The last one is in force and is shown elsewhere. */
  evaluations: Evaluation[];
  versions: ExtractionVersion[];
}

/**
 * Decisions, earlier evaluations and extraction versions in one card. A part with nothing
 * in it is left out, and so is the card when every part is empty. The decisions start
 * open, because they are what a reviewer looks for first.
 */
export function HistoryCard({ decisions, evaluations, versions }: HistoryCardProps) {
  const earlier = evaluations.slice(0, -1).reverse();
  if (decisions.length === 0 && earlier.length === 0 && versions.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="history-title">
      <h2 id="history-title">History</h2>
      {decisions.length > 0 && (
        <HistoryPart title="Decisions" count={decisions.length} open>
          <DecisionList decisions={decisions} />
        </HistoryPart>
      )}
      {earlier.length > 0 && (
        <HistoryPart title="Earlier evaluations" count={earlier.length}>
          <EvaluationList evaluations={earlier} />
        </HistoryPart>
      )}
      {versions.length > 0 && (
        <HistoryPart title="Extraction versions" count={versions.length}>
          <VersionList versions={versions} />
        </HistoryPart>
      )}
    </section>
  );
}

/** AP Manager only. The API refuses anyone else, and that refusal is shown like any error. */
export function AuditTimeline({ invoiceId, refreshKey }: { invoiceId: number; refreshKey: unknown }) {
  const api = useApi();
  const audit = useLoad<AuditEvent[]>(keys.audit(invoiceId), () => api.getAudit(invoiceId), [refreshKey]);
  return (
    <section className="panel" aria-labelledby="audit-title">
      <h2 id="audit-title">Audit trail</h2>
      {audit.loading ? (
        <Loading what="Loading the audit trail" />
      ) : audit.error !== undefined ? (
        <ErrorNotice error={audit.error} onRetry={() => void audit.reload()} />
      ) : !audit.data || audit.data.length === 0 ? (
        <p className="state state-empty">No events recorded.</p>
      ) : (
        <ol className="timeline">
          {audit.data.map((event) => {
            const text = describeAuditEvent(event);
            return (
              <li key={event.id}>
                <strong>{text.title}</strong>
                <span className="muted block">
                  {formatDateTime(event.timestamp)} · {event.actor} ({roleLabel(event.role)})
                </span>
                {text.lines.length > 0 && (
                  <ul className="plain audit-lines">
                    {text.lines.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                )}
                {/* Ids, versions and hashes: kept, but out of the way. */}
                <p className="technical">
                  <code>{event.event_type}</code>
                  {text.technical ? ` · ${text.technical}` : ""}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
