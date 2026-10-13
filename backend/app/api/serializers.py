"""Turn database rows into API responses.

There are two views of an invoice:
  - reviewers (AP Analyst, AP Manager) see everything needed to decide;
  - intake users see the file name, the upload time, the status and plain-language
    reasons. No rule codes, no parameters, no decisions, no audit trail.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.db.invoice_tables import (
    AuditEventRow,
    EvaluationRow,
    ExtractionVersionRow,
    HumanDecisionRow,
    InvoiceRow,
    RuleParametersRow,
)
from app.domain.enums import InvoiceStatus
from app.rules.parameters import RuleParameters
from app.services.corrections import diff_extractions

SUGGESTION_NOTE = "Suggested by the model. It is not a value read from the document and the rules engine does not use it."

# Statuses whose reasons are the rules engine's, which are written in plain language.
_ENGINE_STATUSES = {
    InvoiceStatus.HELD_FOR_REVIEW.value,
    InvoiceStatus.REJECTED.value,
}
_CORRECTED_PREFIX = "Extracted data was corrected by"


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # SQLite returns naive datetimes; they were stored as UTC
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


# ---- intake view


def intake_reasons(invoice: InvoiceRow, evaluation: EvaluationRow | None) -> list[str]:
    """Plain-language reasons for the person who uploaded the document."""
    if invoice.status_reason:
        if invoice.status_reason.startswith(_CORRECTED_PREFIX):
            return ["The extracted data was corrected by a reviewer; a decision is pending."]
        return [invoice.status_reason]
    if evaluation is not None and invoice.status in _ENGINE_STATUSES:
        return [reason["message"] for reason in evaluation.reasons]
    return []


def invoice_for_intake(invoice: InvoiceRow, evaluation: EvaluationRow | None) -> dict:
    return {
        "id": invoice.id,
        "filename": invoice.filename,
        "uploaded_at": _time(invoice.uploaded_at),
        "status": invoice.status,
        "reasons": intake_reasons(invoice, evaluation),
    }


# ---- reviewer view


def invoice_summary(invoice: InvoiceRow, supplier_names: dict[str, str] | None = None) -> dict:
    return {
        "id": invoice.id,
        "filename": invoice.filename,
        "uploaded_by": invoice.uploaded_by,
        "uploaded_at": _time(invoice.uploaded_at),
        "status": invoice.status,
        "status_reason": invoice.status_reason,
        "supplier_id": invoice.supplier_id,
        # Legal name from the supplier master; None if the supplier is unresolved or the
        # master no longer has it.
        "supplier_name": (supplier_names or {}).get(invoice.supplier_id) if invoice.supplier_id else None,
        "invoice_number": invoice.invoice_number,
        "total": _amount(invoice.total),
        "currency": invoice.currency,
    }


MODEL_ORIGIN = {"source": "MODEL"}


def field_origins(versions: list[ExtractionVersionRow]) -> dict[tuple, dict]:
    """Who last set each field: the model, or a person in a correction.

    Keyed by (scope, line_number, field). Fields not in the result came from the model.
    For a corrected field it says who corrected it and what the field was before, so a
    value typed by a person is never shown as if the model had read it.
    """
    origins: dict[tuple, dict] = {}
    for before, after in zip(versions, versions[1:]):
        for change in diff_extractions(before.payload, after.payload):
            key = (change["scope"], change["line_number"], change["field"])
            if after.source != "HUMAN_CORRECTION":
                origins.pop(key, None)  # the model read it again
                continue
            origins[key] = {
                "source": "HUMAN_CORRECTION",
                "corrected_by": after.created_by,
                "corrected_at": _time(after.created_at),
                "version": after.version,
                "previous_value": change["before"]["value"],
                "previous_status": change["before"]["status"],
            }
    return origins


def _read_field(field: dict, origin: dict | None = None) -> dict:
    """A field with its reading status and origin. A suggestion is kept apart and labelled."""
    suggested = field.get("suggested_value")
    return {
        "value": field.get("value"),
        "status": field.get("status"),
        "origin": origin or MODEL_ORIGIN,
        "suggestion": None
        if suggested is None
        else {"value": suggested, "is_suggestion": True, "note": SUGGESTION_NOTE},
    }


def _po_references(field: dict, origin: dict | None = None) -> dict:
    suggested = field.get("suggested_values") or []
    return {
        "values": field.get("values", []),
        "status": field.get("status"),
        "origin": origin or MODEL_ORIGIN,
        "suggestion": None
        if not suggested
        else {"values": suggested, "is_suggestion": True, "note": SUGGESTION_NOTE},
    }


def extraction_detail(
    version: ExtractionVersionRow,
    evaluation: EvaluationRow | None,
    origins: dict[tuple, dict] | None = None,
) -> dict:
    payload = version.payload
    origins = origins or {}

    def plain_origins(scope: str, line_number: int | None, names: tuple[str, ...]) -> dict:
        """Origins of the fields without a reading status, only where a person set them."""
        found = {name: origins.get((scope, line_number, name)) for name in names}
        return {name: origin for name, origin in found.items() if origin}

    header = payload.get("header")
    if not isinstance(header, dict):
        return {**version_summary(version), "data": payload}  # not in the model's format

    mapping = {}
    if evaluation is not None and evaluation.extraction_version_id == version.id:
        mapping = {line["line_number"]: line for line in evaluation.matching.get("lines", [])}

    lines = []
    for number, line in enumerate(payload.get("lines", []), start=1):
        matched = mapping.get(number)
        lines.append(
            {
                "line_number": number,
                "description": line.get("description"),
                "quantity": _read_field(line.get("quantity") or {}, origins.get(("line", number, "quantity"))),
                "unit_price": _read_field(
                    line.get("unit_price") or {}, origins.get(("line", number, "unit_price"))
                ),
                "line_total": line.get("line_total"),
                "po_line_printed_on_document": line.get("po_line_reference"),
                "corrected_fields": plain_origins(
                    "line", number, ("description", "line_total", "po_line_reference")
                ),
                "po_line_mapping": None
                if matched is None
                else {
                    "po_number": matched.get("po_number"),
                    "po_line_number": matched.get("po_line_number"),
                    "method": matched.get("method"),
                },
            }
        )
    return {
        **version_summary(version),
        "document_type": payload.get("document_type"),
        "classification_reason": payload.get("classification_reason"),
        # Fields without a reading status that a person corrected, with who and what it was.
        "corrected_fields": {
            **plain_origins("document", None, ("document_type",)),
            **plain_origins(
                "header",
                None,
                ("supplier_name", "supplier_tax_id", "due_date", "currency", "subtotal", "tax_amount", "total_amount"),
            ),
        },
        "header": {
            "supplier_name": header.get("supplier_name"),
            "supplier_tax_id": header.get("supplier_tax_id"),
            "invoice_number": _read_field(
                header.get("invoice_number") or {}, origins.get(("header", None, "invoice_number"))
            ),
            "invoice_date": _read_field(
                header.get("invoice_date") or {}, origins.get(("header", None, "invoice_date"))
            ),
            "due_date": header.get("due_date"),
            "po_references": _po_references(
                header.get("po_references") or {}, origins.get(("header", None, "po_references"))
            ),
            "currency": header.get("currency"),
            "subtotal": header.get("subtotal"),
            "tax_amount": header.get("tax_amount"),
            "total_amount": header.get("total_amount"),
        },
        "lines": lines,
    }


def version_summary(version: ExtractionVersionRow) -> dict:
    return {
        "version": version.version,
        "source": version.source,
        "created_by": version.created_by,
        "created_at": _time(version.created_at),
        "comment": version.comment,
        "model": version.model,
        "prompt_version": version.prompt_version,
    }


def evaluation_detail(evaluation: EvaluationRow, version_numbers: dict[int, int] | None = None) -> dict:
    matching = evaluation.matching
    return {
        "id": evaluation.id,
        # Which extraction version this run of the engine looked at.
        "extraction_version": (version_numbers or {}).get(evaluation.extraction_version_id),
        "outcome": evaluation.outcome,
        "evaluated_at": _time(evaluation.evaluated_at),
        "reasons": evaluation.reasons,  # code, message, outcome, source, decision_log_ref, line_number
        "parameters_snapshot": evaluation.parameters_snapshot,
        "reference_data_version": evaluation.reference_data_version,
        "matching": {
            "supplier_id": matching.get("supplier_id"),
            "supplier_method": matching.get("supplier_method"),
            "po_references": matching.get("po_references", []),
            "missing_po_numbers": matching.get("missing_po_numbers", []),
            "flags": matching.get("flags", []),
        },
    }


def decision_detail(decision: HumanDecisionRow) -> dict:
    return {
        "id": decision.id,
        "decision": decision.decision,
        "previous_status": decision.previous_outcome,
        "comment": decision.comment,
        "decided_by": decision.decided_by,
        "role": decision.role,
        "required_role": decision.required_role,
        "decided_at": _time(decision.decided_at),
        "superseded": decision.superseded_at is not None,
        "superseded_at": _time(decision.superseded_at),
        "superseded_reason": decision.superseded_reason,
    }


def invoice_for_reviewer(
    invoice: InvoiceRow,
    versions: list[ExtractionVersionRow],
    evaluation: EvaluationRow | None,
    decisions: list[HumanDecisionRow],
    actions: dict,
    evaluations: list[EvaluationRow] | None = None,
    supplier_names: dict[str, str] | None = None,
) -> dict:
    latest = versions[-1] if versions else None
    version_numbers = {version.id: version.version for version in versions}
    return {
        "invoice": invoice_summary(invoice, supplier_names),
        # What the caller may do now, computed by the service for their role.
        "actions": actions,
        "extraction": extraction_detail(latest, evaluation, field_origins(versions)) if latest else None,
        "evaluation": evaluation_detail(evaluation, version_numbers) if evaluation else None,
        # Every run of the rules engine, oldest first; the last one is the one in force.
        # Earlier runs show why the invoice was held or rejected before a correction.
        "evaluation_history": [
            evaluation_detail(item, version_numbers) for item in (evaluations or [])
        ],
        "decisions": [decision_detail(d) for d in decisions],
        "extraction_versions": [version_summary(v) for v in versions],
    }


def audit_event(event: AuditEventRow) -> dict:
    return {
        "id": event.id,
        "invoice_id": event.invoice_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "role": event.role,
        "timestamp": _time(event.timestamp),
        "details": event.details,
    }


def parameters(value: RuleParameters) -> dict:
    return {
        "version": value.version,
        "price_tolerance_pct": str(value.price_tolerance_pct.normalize()),
        "analyst_approval_limit": _amount(value.analyst_approval_limit),
        "rounding_tolerance": _amount(value.rounding_tolerance),
        "enabled_assumption_rules": sorted(value.enabled_assumption_rules, key=lambda c: int(c[1:])),
        "changed_by": value.changed_by,
        "changed_at": _time(value.changed_at),
    }


def parameter_version(row: RuleParametersRow) -> dict:
    return {
        "version": row.version,
        "price_tolerance_pct": str(row.price_tolerance_pct.normalize()),
        "analyst_approval_limit": _amount(row.analyst_approval_limit),
        "rounding_tolerance": _amount(row.rounding_tolerance),
        "enabled_assumption_rules": sorted(row.enabled_assumption_rules, key=lambda c: int(c[1:])),
        "changed_by": row.changed_by,
        "changed_at": _time(row.changed_at),
        "comment": row.comment,
    }
