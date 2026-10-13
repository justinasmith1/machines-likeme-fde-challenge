"""Match an invoice, run the rules engine and store the result.

One evaluation row is written per run and is never changed afterwards. The invoice row
is updated with the fields used for listing and duplicate detection.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.invoice_tables import EvaluationRow, ExtractionVersionRow, InvoiceRow
from app.db.tables import ReferenceSourceRow
from app.extraction.mapper import MappedInvoice
from app.matching.matcher import MatchResult, build_context
from app.rules.engine import Decision, evaluate
from app.rules.parameters import RuleParameters
from app.services.common import record_event
from app.services.parameters import snapshot_to_json


def reference_data_version(session: Session) -> dict[str, str]:
    """sha256 of the reference file version currently loaded, per file."""
    latest: dict[str, ReferenceSourceRow] = {}
    for row in session.scalars(select(ReferenceSourceRow)):
        known = latest.get(row.file_name)
        if known is None or row.loaded_at > known.loaded_at:
            latest[row.file_name] = row
    return {name: row.sha256 for name, row in sorted(latest.items())}


def reasons_to_json(decision: Decision) -> list[dict]:
    return [
        {
            "code": reason.code,
            "message": reason.message,
            "outcome": reason.outcome.value,
            "source": reason.source.value,
            "decision_log_ref": reason.decision_log_ref,
            "line_number": reason.line_number,
        }
        for reason in decision.reasons
    ]


def evaluate_and_store(
    session: Session,
    invoice: InvoiceRow,
    extraction_version: ExtractionVersionRow,
    mapped: MappedInvoice,
    parameters: RuleParameters,
    now: datetime,
) -> tuple[Decision, MatchResult, EvaluationRow]:
    match = build_context(mapped, session, evaluated_at=now, invoice_id=invoice.id)
    decision = evaluate(mapped.invoice, match.context, parameters)

    matching = match.to_json()
    matching["flags"] = list(mapped.flags)
    evaluation = EvaluationRow(
        invoice_id=invoice.id,
        extraction_version_id=extraction_version.id,
        outcome=decision.outcome.value,
        reasons=reasons_to_json(decision),
        parameters_snapshot=snapshot_to_json(decision.parameters_snapshot),
        reference_data_version=reference_data_version(session),
        matching=matching,
        evaluated_at=now,
    )
    session.add(evaluation)

    invoice.supplier_id = match.supplier.supplier_id if match.supplier else None
    invoice.invoice_number = mapped.invoice.invoice_number
    invoice.total = mapped.invoice.total
    invoice.currency = mapped.invoice.currency
    invoice.status = decision.outcome.value
    invoice.status_reason = None
    session.flush()

    record_event(
        session,
        "INVOICE_EVALUATED",
        timestamp=now,
        invoice_id=invoice.id,
        details={
            "evaluation_id": evaluation.id,
            "extraction_version": extraction_version.version,
            "outcome": decision.outcome.value,
            "reason_codes": [reason.code for reason in decision.reasons],
            "parameters_version": parameters.version,
        },
    )
    return decision, match, evaluation
