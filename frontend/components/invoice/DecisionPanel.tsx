"use client";

import { useState, type FormEvent } from "react";
import type { Actions, DecisionName, ReviewerInvoice } from "@/lib/api/types";
import { decisionChoices } from "@/lib/invoices/actions";
import { ErrorNotice, Notice } from "../Feedback";
import { useApi } from "../Providers";

interface DecisionPanelProps {
  invoiceId: number;
  actions: Actions;
  onDecided: (detail: ReviewerInvoice) => void;
}

/**
 * The decisions the backend says apply to this invoice for this user. Which ones appear,
 * and whether the approval limit puts one in the AP Manager's hands, comes from the API:
 * nothing here compares amounts. If the API still refuses (403, 409), the message is shown
 * and the form stays usable.
 */
export function DecisionPanel({ invoiceId, actions, onDecided }: DecisionPanelProps) {
  const api = useApi();
  const choices = decisionChoices(actions);
  const [selected, setSelected] = useState<DecisionName | null>(null);
  const [comment, setComment] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  if (choices.length === 0) {
    return (
      <section className="panel" aria-labelledby="decision-title">
        <h2 id="decision-title">Decision</h2>
        <p className="state state-empty">No decision applies to this document in its current status.</p>
      </section>
    );
  }
  const choice = choices.find((item) => item.decision === selected) ?? null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setDone(null);
    if (!choice) {
      setProblem("Choose a decision.");
      return;
    }
    if (!comment.trim()) {
      setProblem("A comment is required to record a decision.");
      return;
    }
    setProblem(null);
    setSending(true);
    try {
      const detail = await api.decide(invoiceId, choice.decision, comment.trim());
      setDone(`Decision recorded: ${choice.label}.`);
      setComment("");
      setSelected(null);
      onDecided(detail);
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="panel panel-primary" aria-labelledby="decision-title">
      <h2 id="decision-title">Decision</h2>
      <form onSubmit={submit}>
        <fieldset>
          <legend>What should happen to this invoice?</legend>
          {choices.map((item) => (
            <div key={item.decision} className="choice">
              <label>
                <input
                  type="radio"
                  name="decision"
                  value={item.decision}
                  checked={selected === item.decision}
                  disabled={!item.enabled}
                  onChange={() => {
                    setSelected(item.decision);
                    setProblem(null);
                  }}
                />{" "}
                {item.label}
              </label>
              {item.notice && (
                <p className={item.enabled ? "hint" : "hint hint-blocked"}>
                  {item.enabled ? "" : "Not available to you. "}
                  {item.notice}
                </p>
              )}
              {item.warning && (
                // Advice from the API. It informs; it does not disable the decision.
                <p className="hint hint-warning" role="note">
                  <span aria-hidden="true">⚠</span> {item.warning}
                </p>
              )}
            </div>
          ))}
        </fieldset>
        <label htmlFor="decision-comment">Comment (required)</label>
        <textarea
          id="decision-comment"
          value={comment}
          rows={3}
          onChange={(event) => setComment(event.target.value)}
          aria-describedby="decision-effect"
        />
        <p id="decision-effect" className="hint">
          {choice ? choice.effect : "Your comment is stored with the decision and shown in its history."}
        </p>
        <button type="submit" disabled={sending}>
          {sending ? "Recording…" : "Record decision"}
        </button>
      </form>
      {problem && (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
      {done && <Notice tone="success">{done}</Notice>}
    </section>
  );
}
