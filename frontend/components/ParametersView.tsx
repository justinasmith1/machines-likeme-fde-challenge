"use client";

import { useEffect, useState, type FormEvent } from "react";
import type { RuleParameters } from "@/lib/api/types";
import type { Session } from "@/lib/auth";
import { formatAmount, formatDateTime, formatPercent } from "@/lib/format";
import { keys, parametersChanged, useLoad } from "@/lib/hooks";
import { canEditParameters } from "@/lib/invoices/actions";
import { Empty, ErrorNotice, Loading, Notice } from "./Feedback";
import { useApi } from "./Providers";

const ASSUMPTION_RULES: Array<{ code: string; label: string }> = [
  { code: "A1", label: "Hold when the supplier is inactive" },
  { code: "A2", label: "Reject when the purchase order is closed" },
  { code: "A3", label: "Hold when invoiced quantity exceeds ordered quantity" },
  { code: "A4", label: "Hold when the invoice currency differs from the purchase order" },
  { code: "A5", label: "Hold when totals do not add up" },
  { code: "A8", label: "Hold when cumulative invoiced quantity exceeds received" },
  { code: "A10", label: "Hold when the invoice references more than one purchase order" },
];

function ParameterSummary({ parameters }: { parameters: RuleParameters }) {
  return (
    <dl className="fields">
      <dt>Version</dt>
      <dd>
        {parameters.version}
        <span className="muted">
          {" "}
          · changed by {parameters.changed_by ?? "–"}, {formatDateTime(parameters.changed_at)}
        </span>
      </dd>
      <dt>Price tolerance</dt>
      <dd>{formatPercent(parameters.price_tolerance_pct)} of the purchase order unit price, per line</dd>
      <dt>Analyst approval limit</dt>
      <dd>{formatAmount(parameters.analyst_approval_limit)} (above it, the AP Manager decides)</dd>
      <dt>Rounding tolerance</dt>
      <dd>{formatAmount(parameters.rounding_tolerance)} on totals and lines</dd>
      <dt>Always on</dt>
      <dd>
        The rules from the specification, and three fixed rules. An invoice is held when a quantity or
        unit price is illegible <code>A6</code>, when the purchase order number is illegible{" "}
        <code>A7</code>, or when the invoice number or date is missing or illegible <code>A9</code>. They
        cannot be switched off. Holding an illegible field was confirmed by the client (DL-18); holding a
        missing invoice number or date is an assumption (DL-35).
      </dd>
      <dt>Additional checks enabled</dt>
      <dd>
        <ul className="plain">
          {ASSUMPTION_RULES.map((rule) => (
            <li key={rule.code}>
              {parameters.enabled_assumption_rules.includes(rule.code) ? "✓ On" : "– Off"}: {rule.label}{" "}
              <code>{rule.code}</code>
            </li>
          ))}
        </ul>
      </dd>
    </dl>
  );
}

function ParameterEditor({ current, onSaved }: { current: RuleParameters; onSaved: (next: RuleParameters) => void }) {
  const api = useApi();
  const [tolerance, setTolerance] = useState(current.price_tolerance_pct);
  const [limit, setLimit] = useState(current.analyst_approval_limit);
  const [rounding, setRounding] = useState(current.rounding_tolerance);
  const [rules, setRules] = useState<string[]>(current.enabled_assumption_rules);
  const [comment, setComment] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setTolerance(current.price_tolerance_pct);
    setLimit(current.analyst_approval_limit);
    setRounding(current.rounding_tolerance);
    setRules(current.enabled_assumption_rules);
  }, [current]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setSaved(null);
    const values: Record<string, unknown> = {};
    if (tolerance.trim() !== current.price_tolerance_pct) values.price_tolerance_pct = tolerance.trim();
    if (limit.trim() !== current.analyst_approval_limit) values.analyst_approval_limit = limit.trim();
    if (rounding.trim() !== current.rounding_tolerance) values.rounding_tolerance = rounding.trim();
    const sameRules =
      [...rules].sort().join(",") === [...current.enabled_assumption_rules].sort().join(",");
    if (!sameRules) values.enabled_assumption_rules = rules;

    if (Object.keys(values).length === 0) {
      setProblem("Nothing was changed.");
      return;
    }
    if (!comment.trim()) {
      setProblem("A comment explaining the change is required.");
      return;
    }
    setProblem(null);
    setSending(true);
    try {
      const next = await api.updateParameters(values, comment.trim());
      setComment("");
      setSaved(next.version);
      onSaved(next);
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="edit-title">
      <h2 id="edit-title">Change parameters</h2>
      <p className="hint">
        A change creates a new version and applies only to invoices evaluated from now on. Invoices already
        evaluated keep the values that applied to them.
      </p>
      <form onSubmit={submit}>
        <div className="formgrid">
          <div>
            <label htmlFor="param-tolerance">Price tolerance (fraction, 0.02 = 2%)</label>
            <input id="param-tolerance" value={tolerance} onChange={(e) => setTolerance(e.target.value)} />
          </div>
          <div>
            <label htmlFor="param-limit">Analyst approval limit</label>
            <input id="param-limit" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </div>
          <div>
            <label htmlFor="param-rounding">Rounding tolerance</label>
            <input id="param-rounding" value={rounding} onChange={(e) => setRounding(e.target.value)} />
          </div>
        </div>
        <fieldset>
          <legend>Additional checks (rules from the specification, A6, A7 and A9 are always on)</legend>
          {ASSUMPTION_RULES.map((rule) => (
            <label key={rule.code} className="check">
              <input
                type="checkbox"
                checked={rules.includes(rule.code)}
                onChange={(event) =>
                  setRules(event.target.checked ? [...rules, rule.code] : rules.filter((c) => c !== rule.code))
                }
              />{" "}
              {rule.label} <code>{rule.code}</code>
            </label>
          ))}
        </fieldset>
        <label htmlFor="param-comment">Comment (required)</label>
        <textarea id="param-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
        <button type="submit" disabled={sending}>
          {sending ? "Saving…" : "Save new version"}
        </button>
      </form>
      {problem && (
        <div className="notice notice-error" role="alert">
          {problem}
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
      {saved !== null && <Notice tone="success">Version {saved} saved. It applies to new evaluations.</Notice>}
    </section>
  );
}

function ParameterHistory({ refreshKey }: { refreshKey: unknown }) {
  const api = useApi();
  const history = useLoad(keys.parameterHistory, () => api.parameterHistory(), [refreshKey]);
  return (
    <section className="panel" aria-labelledby="history-title">
      <h2 id="history-title">Version history</h2>
      {history.loading ? (
        <Loading what="Loading the history" />
      ) : history.error !== undefined ? (
        <ErrorNotice error={history.error} onRetry={() => void history.reload()} />
      ) : !history.data || history.data.length === 0 ? (
        <Empty>No versions recorded.</Empty>
      ) : (
        <table>
          <caption className="sr-only">Every version of the parameters, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Changed</th>
              <th scope="col">Tolerance</th>
              <th scope="col" className="num">
                Approval limit
              </th>
              <th scope="col" className="num">
                Rounding
              </th>
              <th scope="col">Checks on</th>
              <th scope="col">Comment</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((version) => (
              <tr key={version.version}>
                <td>{version.version}</td>
                <td>
                  {formatDateTime(version.changed_at)}
                  <span className="muted block">by {version.changed_by ?? "–"}</span>
                </td>
                <td>{formatPercent(version.price_tolerance_pct)}</td>
                <td className="num">{formatAmount(version.analyst_approval_limit)}</td>
                <td className="num">{formatAmount(version.rounding_tolerance)}</td>
                <td>{version.enabled_assumption_rules.join(", ") || "none"}</td>
                <td>{version.comment ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ParametersView({ session }: { session: Session }) {
  const api = useApi();
  const current = useLoad(keys.parameters, () => api.getParameters());
  const editable = canEditParameters(session.role);

  if (current.loading) return <Loading what="Loading parameters" />;
  if (current.error !== undefined && !current.data) {
    return <ErrorNotice error={current.error} onRetry={() => void current.reload()} />;
  }
  if (!current.data) return null;

  return (
    <>
      <section className="panel" aria-labelledby="current-title">
        <h2 id="current-title">Parameters in force</h2>
        <ParameterSummary parameters={current.data} />
        {!editable && <p className="hint">Only the AP Manager can change these values.</p>}
      </section>
      {editable && (
        <>
          <ParameterEditor current={current.data} onSaved={(next) => parametersChanged(next)} />
          <ParameterHistory refreshKey={current.data.version} />
        </>
      )}
    </>
  );
}
