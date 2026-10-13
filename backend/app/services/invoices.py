"""Business operations on invoices. No HTTP here: the API (phase 4B) calls these.

Permissions are checked in this layer, not only in the API, and failures raise typed
errors. Every change of status writes an audit event. Each public method commits.

Status flow:
  submit_invoice      -> PENDING_EXTRACTION
  process_invoice     -> NOT_AN_INVOICE | EXTRACTION_FAILED | AUTO_PAY | HELD_FOR_REVIEW | REJECTED
  retry_extraction    EXTRACTION_FAILED -> PENDING_EXTRACTION
  correct_extraction  new extraction version, evaluated again; the invoice is then
                      HELD_FOR_REVIEW until a person decides, whatever the engine says (DL-46)
  decide              any final status -> APPROVED_FOR_PAYMENT | OVERRIDDEN |
                      REJECTED_BY_REVIEWER | CREDIT_NOTE_REQUESTED (DL-48)

Example: PENDING_EXTRACTION -> HELD_FOR_REVIEW -> (correction) HELD_FOR_REVIEW ->
(approval) APPROVED_FOR_PAYMENT.
"""

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pydantic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.invoice_tables import (
    AuditEventRow,
    EvaluationRow,
    ExtractionVersionRow,
    HumanDecisionRow,
    InvoiceRow,
)
from app.domain.enums import ExtractionSource, HumanDecision, InvoiceStatus, Role
from app.extraction.mapper import to_engine_input
from app.extraction.schema import ModelExtraction
from app.extraction.service import ExtractionResult, ExtractionStatus
from app.rules.parameters import RuleParameters
from app.services import parameters as parameter_service
from app.services.common import User, record_event, utc_now
from app.services.corrections import apply_corrections, diff_extractions
from app.services.errors import InvalidState, NotFound, PermissionDenied, ValidationError
from app.services.evaluation import evaluate_and_store
from app.storage.files import FileStore

Extractor = Callable[[bytes, str], ExtractionResult]

logger = logging.getLogger(__name__)

PROCESSING_ERROR = "PROCESSING_ERROR"
_PROCESSING_ERROR_MESSAGE = (
    "The document could not be processed because of an unexpected error. It can be retried."
)

# Statuses a person can decide on: every final status of an invoice (DL-48).
# Not decidable: PENDING_EXTRACTION, EXTRACTION_FAILED and NOT_AN_INVOICE.
_DECIDABLE_STATUSES = {
    InvoiceStatus.AUTO_PAY.value,
    InvoiceStatus.HELD_FOR_REVIEW.value,
    InvoiceStatus.REJECTED.value,
    InvoiceStatus.APPROVED_FOR_PAYMENT.value,
    InvoiceStatus.REJECTED_BY_REVIEWER.value,
    InvoiceStatus.CREDIT_NOTE_REQUESTED.value,
    InvoiceStatus.OVERRIDDEN.value,
}
# Already payable: approving again would change nothing.
_PAYABLE_STATUSES = {
    InvoiceStatus.AUTO_PAY.value,
    InvoiceStatus.APPROVED_FOR_PAYMENT.value,
    InvoiceStatus.OVERRIDDEN.value,
}
_STATUS_AFTER_DECISION = {
    HumanDecision.APPROVE: InvoiceStatus.APPROVED_FOR_PAYMENT,
    HumanDecision.REJECT: InvoiceStatus.REJECTED_BY_REVIEWER,
    HumanDecision.REQUEST_CREDIT_NOTE: InvoiceStatus.CREDIT_NOTE_REQUESTED,
    HumanDecision.OVERRIDE: InvoiceStatus.OVERRIDDEN,
}
# Decisions that release payment, and therefore are subject to the approval limit.
_PAYMENT_DECISIONS = {HumanDecision.APPROVE, HumanDecision.OVERRIDE}
# Order in which decisions are offered: the one that releases payment first.
_DECISION_ORDER = (
    HumanDecision.APPROVE,
    HumanDecision.OVERRIDE,
    HumanDecision.REJECT,
    HumanDecision.REQUEST_CREDIT_NOTE,
)
_NO_PURCHASE_ORDER_WARNING = (
    "No purchase order is associated with this invoice: none of its lines is matched to a "
    "purchase order line. Consider correcting the purchase order number first."
)
SUPERSEDED_BY_DECISION = "NEW_DECISION"
SUPERSEDED_BY_CORRECTION = "EXTRACTION_CORRECTED"
_REVIEWER_ROLES = {Role.AP_ANALYST, Role.AP_MANAGER}


class InvoiceService:
    def __init__(
        self,
        session: Session,
        *,
        file_store: FileStore,
        extractor: Extractor,
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._files = file_store
        self._extract = extractor
        self._settings = settings
        self._clock = clock

    # ---- queries

    def get_invoice(self, invoice_id: int) -> InvoiceRow:
        invoice = self._session.get(InvoiceRow, invoice_id)
        if invoice is None:
            raise NotFound(f"Invoice {invoice_id} does not exist.")
        return invoice

    def latest_extraction(self, invoice_id: int) -> ExtractionVersionRow | None:
        return self._session.scalars(
            select(ExtractionVersionRow)
            .where(ExtractionVersionRow.invoice_id == invoice_id)
            .order_by(ExtractionVersionRow.version.desc())
            .limit(1)
        ).first()

    def latest_evaluation(self, invoice_id: int) -> EvaluationRow | None:
        return self._session.scalars(
            select(EvaluationRow)
            .where(EvaluationRow.invoice_id == invoice_id)
            .order_by(EvaluationRow.id.desc())
            .limit(1)
        ).first()

    def audit_trail(self, invoice_id: int) -> list[AuditEventRow]:
        return list(
            self._session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.invoice_id == invoice_id)
                .order_by(AuditEventRow.id)
            )
        )

    def current_parameters(self) -> RuleParameters:
        parameters = parameter_service.current_parameters(
            self._session, self._settings, self._clock()
        )
        self._session.commit()
        return parameters

    # ---- intake

    def submit_invoice(self, file_bytes: bytes, filename: str, user: User) -> InvoiceRow:
        """Store the file and register the invoice. Extraction runs later (process_invoice)."""
        if not filename or not filename.strip():
            raise ValidationError("The file has no name.")
        now = self._clock()
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        stored_path = self._files.save(sha256, Path(filename).suffix, file_bytes)
        invoice = InvoiceRow(
            filename=Path(filename).name,
            file_sha256=sha256,
            stored_path=stored_path,
            uploaded_by=user.username,
            uploaded_at=now,
            status=InvoiceStatus.PENDING_EXTRACTION.value,
        )
        self._session.add(invoice)
        self._session.flush()
        record_event(
            self._session,
            "INVOICE_SUBMITTED",
            timestamp=now,
            actor=user.username,
            role=user.role,
            invoice_id=invoice.id,
            details={"filename": invoice.filename, "file_sha256": sha256},
        )
        self._session.commit()
        return invoice

    def process_invoice(self, invoice_id: int) -> InvoiceRow:
        """Extract and evaluate a pending invoice. Safe to run in a background task.

        An unexpected error never leaves the invoice pending: it becomes EXTRACTION_FAILED
        with code PROCESSING_ERROR, an audit event is written, and it can be retried (DL-59).
        """
        invoice = self.get_invoice(invoice_id)
        if invoice.status != InvoiceStatus.PENDING_EXTRACTION.value:
            raise InvalidState(
                f"Invoice {invoice_id} is {invoice.status}; only a pending invoice can be processed."
            )
        try:
            return self._extract_and_evaluate(invoice)
        except Exception as exc:
            # The detail goes to the server log only. What is stored and shown is a fixed,
            # plain message: no internals and nothing that came from the failing code.
            logger.exception("Unexpected error while processing invoice %s", invoice_id)
            self._session.rollback()
            invoice = self.get_invoice(invoice_id)
            invoice.status = InvoiceStatus.EXTRACTION_FAILED.value
            invoice.status_reason = _PROCESSING_ERROR_MESSAGE
            record_event(
                self._session,
                "EXTRACTION_FAILED",
                timestamp=self._clock(),
                invoice_id=invoice.id,
                details={
                    "error_code": PROCESSING_ERROR,
                    "errors": [_PROCESSING_ERROR_MESSAGE],
                    "error_type": type(exc).__name__,
                },
            )
            self._session.commit()
            return invoice

    def _extract_and_evaluate(self, invoice: InvoiceRow) -> InvoiceRow:
        result = self._extract(self._files.load(invoice.stored_path), invoice.filename)
        now = self._clock()

        if result.status is ExtractionStatus.EXTRACTION_FAILED:
            invoice.status = InvoiceStatus.EXTRACTION_FAILED.value
            invoice.status_reason = "; ".join(result.errors) or "The document could not be read."
            record_event(
                self._session,
                "EXTRACTION_FAILED",
                timestamp=now,
                invoice_id=invoice.id,
                details={"error_code": result.error_code, "errors": result.errors},
            )
            self._session.commit()
            return invoice

        version = self._add_extraction_version(
            invoice,
            source=ExtractionSource.MODEL,
            payload=result.extraction.model_dump(mode="json"),
            raw_response=result.raw_response,
            prompt_version=result.prompt_version,
            model=result.model,
            created_by="system",
            comment=None,
            now=now,
        )
        record_event(
            self._session,
            "EXTRACTION_SUCCEEDED",
            timestamp=now,
            invoice_id=invoice.id,
            details={
                "extraction_version": version.version,
                "document_type": result.document_type,
                "from_cache": result.from_cache,
                "tokens": result.tokens,
                "model": result.model,
                "prompt_version": result.prompt_version,
            },
        )
        self._evaluate_version(invoice, version, result.extraction, now)
        self._session.commit()
        return invoice

    def retry_extraction(self, invoice_id: int, user: User) -> InvoiceRow:
        """Put a failed invoice back in the queue. The caller then runs process_invoice."""
        invoice = self.get_invoice(invoice_id)
        if invoice.status != InvoiceStatus.EXTRACTION_FAILED.value:
            raise InvalidState(
                f"Invoice {invoice_id} is {invoice.status}; only a failed extraction can be retried."
            )
        now = self._clock()
        previous_reason = invoice.status_reason
        invoice.status = InvoiceStatus.PENDING_EXTRACTION.value
        invoice.status_reason = None
        record_event(
            self._session,
            "EXTRACTION_RETRY_REQUESTED",
            timestamp=now,
            actor=user.username,
            role=user.role,
            invoice_id=invoice.id,
            details={"previous_error": previous_reason},
        )
        self._session.commit()
        return invoice

    # ---- review

    def correct_extraction(
        self, invoice_id: int, corrections: dict, comment: str, user: User
    ) -> InvoiceRow:
        """Record a corrected extraction as a new version and evaluate it again (DL-12).

        Correcting data never approves payment (DL-46). The evaluation is stored with the
        engine outcome, but the invoice stays HELD_FOR_REVIEW until a person decides,
        whether or not there was a decision before. The same person may correct and then
        approve: they are two separate actions, each with its comment and audit event.
        """
        invoice = self.get_invoice(invoice_id)
        if user.role not in _REVIEWER_ROLES:
            raise PermissionDenied("Only an AP Analyst or the AP Manager can correct an extraction.")
        if not comment or not comment.strip():
            raise ValidationError("A comment explaining the correction is required.")
        previous = self.latest_extraction(invoice_id)
        if previous is None:
            raise InvalidState(
                f"Invoice {invoice_id} is {invoice.status} and has no extraction to correct."
            )

        try:
            extraction = ModelExtraction.model_validate(
                apply_corrections(previous.payload, corrections)
            )
        except pydantic.ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            )
            raise ValidationError(f"The corrected data is not valid ({problems}).") from exc

        now = self._clock()
        previous_status = invoice.status
        version = self._add_extraction_version(
            invoice,
            source=ExtractionSource.HUMAN_CORRECTION,
            payload=extraction.model_dump(mode="json"),
            raw_response=None,
            prompt_version=None,
            model=None,
            created_by=user.username,
            comment=comment.strip(),
            now=now,
        )
        record_event(
            self._session,
            "EXTRACTION_CORRECTED",
            timestamp=now,
            actor=user.username,
            role=user.role,
            invoice_id=invoice.id,
            details={
                "extraction_version": version.version,
                "previous_version": previous.version,
                "corrections": corrections,
                # Field by field, what it was and what it is now.
                "changes": diff_extractions(previous.payload, version.payload),
                "comment": comment.strip(),
            },
        )
        # A decision taken on the previous data no longer stands.
        self._supersede_decisions(
            invoice, user, now, SUPERSEDED_BY_CORRECTION, previous_status=previous_status
        )
        self._evaluate_version(invoice, version, extraction, now)
        if invoice.status != InvoiceStatus.NOT_AN_INVOICE.value:
            engine_outcome = invoice.status
            invoice.status = InvoiceStatus.HELD_FOR_REVIEW.value
            invoice.status_reason = (
                f"Extracted data was corrected by {user.username}; "
                "a human decision is required before payment."
            )
            record_event(
                self._session,
                "HELD_AFTER_CORRECTION",
                timestamp=now,
                actor=user.username,
                role=user.role,
                invoice_id=invoice.id,
                details={"engine_outcome": engine_outcome, "extraction_version": version.version},
            )
        self._session.commit()
        return invoice

    def decide(self, invoice_id: int, decision: str, comment: str, user: User) -> InvoiceRow:
        """Record a human decision on an invoice in any final status (DL-13, DL-48).

        A new decision replaces the one in force, which stays in the history marked as
        superseded. Releasing payment is APPROVE, or OVERRIDE when the engine rejected the
        invoice; both are subject to the analyst approval limit (DL-49).
        """
        invoice = self.get_invoice(invoice_id)
        now = self._clock()

        if user.role not in _REVIEWER_ROLES:
            self._deny(invoice, decision, user, now, "role cannot decide on invoices")
            raise PermissionDenied("Only an AP Analyst or the AP Manager can decide on an invoice.")
        try:
            decision = HumanDecision(decision)
        except ValueError:
            raise ValidationError(
                f"Unknown decision '{decision}'. Use one of: {', '.join(d.value for d in HumanDecision)}."
            ) from None
        if not comment or not comment.strip():
            raise ValidationError("A comment is required to record a decision.")
        evaluation = self.latest_evaluation(invoice_id)
        problem = self._decision_problem(invoice, evaluation, decision)
        if problem is not None:
            raise InvalidState(problem)

        parameters = parameter_service.current_parameters(self._session, self._settings, now)
        required_role = self._required_role(invoice, decision, parameters)
        if required_role is Role.AP_MANAGER and user.role is not Role.AP_MANAGER:
            self._deny(invoice, decision.value, user, now, "above the analyst approval limit")
            raise PermissionDenied(self._approval_limit_notice(invoice, parameters))

        previous_outcome = invoice.status
        superseded = self._supersede_decisions(
            invoice, user, now, SUPERSEDED_BY_DECISION, previous_status=previous_outcome
        )
        row = HumanDecisionRow(
            invoice_id=invoice.id,
            evaluation_id=evaluation.id,
            decision=decision.value,
            previous_outcome=previous_outcome,
            comment=comment.strip(),
            decided_by=user.username,
            role=user.role.value,
            required_role=required_role.value,
            decided_at=now,
        )
        self._session.add(row)
        invoice.status = _STATUS_AFTER_DECISION[decision].value
        invoice.status_reason = None
        self._session.flush()
        record_event(
            self._session,
            "HUMAN_DECISION_RECORDED",
            timestamp=now,
            actor=user.username,
            role=user.role,
            invoice_id=invoice.id,
            details={
                "human_decision_id": row.id,
                "decision": decision.value,
                "previous_outcome": previous_outcome,
                "new_status": invoice.status,
                "evaluation_id": evaluation.id,
                "required_role": required_role.value,
                "comment": comment.strip(),
                "supersedes_decision_ids": superseded,
            },
        )
        self._session.commit()
        return invoice

    def allowed_actions(self, invoice_id: int, user: User) -> dict:
        """What this user can do with this invoice right now.

        The same rules decide() and the other operations enforce, so a client can show the
        right buttons without repeating them. For each decision that applies to the
        invoice it says whether this user may take it, which role it requires, and a
        notice when the approval limit puts it in the AP Manager's hands (DL-49).
        Showing or hiding a button is a convenience: the operations check again.
        """
        invoice = self.get_invoice(invoice_id)
        if user.role not in _REVIEWER_ROLES:
            return {"decisions": [], "can_correct": False, "can_retry": False}

        evaluation = self.latest_evaluation(invoice_id)
        parameters = parameter_service.current_parameters(
            self._session, self._settings, self._clock()
        )
        self._session.commit()

        decisions = []
        for decision in _DECISION_ORDER:
            if self._decision_problem(invoice, evaluation, decision) is not None:
                continue
            required_role = self._required_role(invoice, decision, parameters)
            needs_manager = required_role is Role.AP_MANAGER
            decisions.append(
                {
                    "decision": decision.value,
                    "allowed": not needs_manager or user.role is Role.AP_MANAGER,
                    "required_role": required_role.value,
                    "notice": self._approval_limit_notice(invoice, parameters)
                    if needs_manager
                    else None,
                    # Advice that does not block the decision.
                    "warning": _NO_PURCHASE_ORDER_WARNING
                    if decision in _PAYMENT_DECISIONS and not self._has_matched_po_line(evaluation)
                    else None,
                }
            )
        return {
            "decisions": decisions,
            "can_correct": self.latest_extraction(invoice_id) is not None,
            "can_retry": invoice.status == InvoiceStatus.EXTRACTION_FAILED.value,
        }

    # ---- parameters

    def update_parameters(self, values: dict, comment: str, user: User) -> RuleParameters:
        parameters = parameter_service.update_parameters(
            self._session, self._settings, values, comment, user, self._clock()
        )
        self._session.commit()
        return parameters

    # ---- decision rules, shared by decide() and allowed_actions()

    @staticmethod
    def _decision_problem(
        invoice: InvoiceRow, evaluation: EvaluationRow | None, decision: HumanDecision
    ) -> str | None:
        """Why the decision does not apply to the invoice in its status, or None if it does."""
        if invoice.status == InvoiceStatus.NOT_AN_INVOICE.value:
            return "This document is not an invoice and can never be approved for payment."
        if invoice.status not in _DECIDABLE_STATUSES or evaluation is None:
            return f"Invoice {invoice.id} is {invoice.status}; it cannot be decided on yet."
        # Paying against an engine rejection is an override; otherwise it is an approval.
        engine_rejected = evaluation.outcome == InvoiceStatus.REJECTED.value
        if decision is HumanDecision.APPROVE and engine_rejected:
            return "The rules engine rejected this invoice. Use OVERRIDE to release payment."
        if decision is HumanDecision.OVERRIDE and not engine_rejected:
            return "OVERRIDE applies only to an invoice the rules engine rejected. Use APPROVE."
        if _STATUS_AFTER_DECISION[decision].value == invoice.status or (
            decision in _PAYMENT_DECISIONS and invoice.status in _PAYABLE_STATUSES
        ):
            return f"Invoice {invoice.id} is already {invoice.status}; the decision would change nothing."
        return None

    @staticmethod
    def _required_role(
        invoice: InvoiceRow, decision: HumanDecision, parameters: RuleParameters
    ) -> Role:
        """Releasing payment above the analyst limit, or with no readable total, is the
        AP Manager's decision (DL-49). Refusing payment is open to analysts."""
        if decision in _PAYMENT_DECISIONS and (
            invoice.total is None or invoice.total > parameters.analyst_approval_limit
        ):
            return Role.AP_MANAGER
        return Role.AP_ANALYST

    @staticmethod
    def _has_matched_po_line(evaluation: EvaluationRow | None) -> bool:
        """True when at least one invoice line was matched to a purchase order line."""
        lines = (evaluation.matching.get("lines") if evaluation is not None else None) or []
        return any(line.get("po_number") for line in lines)

    @staticmethod
    def _approval_limit_notice(invoice: InvoiceRow, parameters: RuleParameters) -> str:
        amount = "unknown" if invoice.total is None else f"{invoice.total:,.2f}"
        return (
            f"The invoice total ({amount}) is above the analyst approval limit "
            f"({parameters.analyst_approval_limit:,.2f}). The AP Manager must decide."
        )

    # ---- internals

    def _deny(self, invoice: InvoiceRow, decision, user: User, now: datetime, why: str) -> None:
        """Audit a refused attempt. Committed on its own, because the caller then raises."""
        record_event(
            self._session,
            "DECISION_DENIED",
            timestamp=now,
            actor=user.username,
            role=user.role,
            invoice_id=invoice.id,
            details={"decision": str(decision), "status": invoice.status, "reason": why},
        )
        self._session.commit()

    def decisions(self, invoice_id: int) -> list[HumanDecisionRow]:
        """Every decision on the invoice, oldest first, including superseded ones."""
        return list(
            self._session.scalars(
                select(HumanDecisionRow)
                .where(HumanDecisionRow.invoice_id == invoice_id)
                .order_by(HumanDecisionRow.id)
            )
        )

    def _supersede_decisions(
        self, invoice: InvoiceRow, user: User, now: datetime, reason: str, *, previous_status: str
    ) -> list[int]:
        """Mark the decision in force as superseded. Decisions are never deleted."""
        active = [d for d in self.decisions(invoice.id) if d.superseded_at is None]
        for decision in active:
            decision.superseded_at = now
            decision.superseded_reason = reason
        if active:
            record_event(
                self._session,
                "HUMAN_DECISION_SUPERSEDED",
                timestamp=now,
                actor=user.username,
                role=user.role,
                invoice_id=invoice.id,
                details={
                    "human_decision_ids": [d.id for d in active],
                    "previous_status": previous_status,
                    "reason": reason,
                },
            )
        return [d.id for d in active]

    def _add_extraction_version(self, invoice: InvoiceRow, *, source, now, **fields):
        previous = self.latest_extraction(invoice.id)
        version = ExtractionVersionRow(
            invoice_id=invoice.id,
            version=(previous.version if previous else 0) + 1,
            source=source.value,
            created_at=now,
            **fields,
        )
        self._session.add(version)
        self._session.flush()
        return version

    def _evaluate_version(
        self,
        invoice: InvoiceRow,
        version: ExtractionVersionRow,
        extraction: ModelExtraction,
        now: datetime,
    ) -> None:
        """Only an invoice reaches the engine. Anything else is NOT_AN_INVOICE (DL-16)."""
        if extraction.document_type != "invoice":
            invoice.status = InvoiceStatus.NOT_AN_INVOICE.value
            invoice.status_reason = (
                f"Received, not an invoice: {extraction.document_type} "
                f"({extraction.classification_reason})"
            )
            record_event(
                self._session,
                "CLASSIFIED_NOT_AN_INVOICE",
                timestamp=now,
                invoice_id=invoice.id,
                details={
                    "extraction_version": version.version,
                    "document_type": extraction.document_type,
                    "classification_reason": extraction.classification_reason,
                },
            )
            return
        parameters = parameter_service.current_parameters(self._session, self._settings, now)
        evaluate_and_store(
            self._session, invoice, version, to_engine_input(extraction), parameters, now
        )
