"""Tables for invoices, extractions, evaluations, human decisions, audit and parameters.

The schema is created with Base.metadata.create_all. That creates missing tables but does
not alter existing ones: during development, delete the local database after a schema
change. A migration tool (Alembic) is needed once there is data to keep.

invoices.supplier_id is a plain value, not a foreign key: reloading reference data
replaces the reference rows (DL-23) and must neither be blocked by invoice history nor
cascade into it.

JSON columns hold amounts as decimal strings, never floats.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.tables import MONEY, Base

UTC_DATETIME = DateTime(timezone=True)


class InvoiceRow(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    stored_path: Mapped[str] = mapped_column(String(512))
    uploaded_by: Mapped[str] = mapped_column(String(128))
    uploaded_at: Mapped[datetime] = mapped_column(UTC_DATETIME)
    status: Mapped[str] = mapped_column(String(32), index=True)
    # Filled in from the latest evaluation, for listing and duplicate detection.
    supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    total: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Plain-language reason when there is no engine decision (not an invoice, failed).
    status_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ExtractionVersionRow(Base):
    """Every extraction of an invoice: the one from the model and each human correction."""

    __tablename__ = "extraction_versions"
    __table_args__ = (UniqueConstraint("invoice_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))  # MODEL | HUMAN_CORRECTION
    payload: Mapped[dict] = mapped_column(JSON)  # validated extraction
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # model answer, no key
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME)
    comment: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class EvaluationRow(Base):
    """One run of the rules engine. Never modified after it is written."""

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    extraction_version_id: Mapped[int] = mapped_column(ForeignKey("extraction_versions.id"))
    outcome: Mapped[str] = mapped_column(String(32))
    reasons: Mapped[list] = mapped_column(JSON)  # code, message, outcome, source, DL, line
    parameters_snapshot: Mapped[dict] = mapped_column(JSON)  # DL-11
    reference_data_version: Mapped[dict] = mapped_column(JSON)  # file name -> sha256
    # How the invoice was matched: supplier, purchase orders and, per line, the PO line,
    # the method used and the quantity. Approved quantities are summed from here (DL-21).
    matching: Mapped[dict] = mapped_column(JSON)
    evaluated_at: Mapped[datetime] = mapped_column(UTC_DATETIME)


class HumanDecisionRow(Base):
    """Decisions by people. The one in force is the one that is not superseded."""

    __tablename__ = "human_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id"))
    decision: Mapped[str] = mapped_column(String(32))  # APPROVE | REJECT | REQUEST_CREDIT_NOTE | OVERRIDE
    previous_outcome: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(String(2000))
    decided_by: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    required_role: Mapped[str] = mapped_column(String(32))
    decided_at: Mapped[datetime] = mapped_column(UTC_DATETIME)
    # A later decision, or a correction of the data, supersedes this one. The row stays.
    superseded_at: Mapped[datetime | None] = mapped_column(UTC_DATETIME, nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AuditEventRow(Base):
    """Append-only audit trail. Rows are inserted, never updated or deleted."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(UTC_DATETIME)
    details: Mapped[dict] = mapped_column(JSON)


class RuleParametersRow(Base):
    """Versions of the rule parameters. The current one is the highest version (DL-11)."""

    __tablename__ = "rule_parameters"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    price_tolerance_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    analyst_approval_limit: Mapped[Decimal] = mapped_column(MONEY)
    rounding_tolerance: Mapped[Decimal] = mapped_column(MONEY)
    enabled_assumption_rules: Mapped[list] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(128))
    changed_at: Mapped[datetime] = mapped_column(UTC_DATETIME)
    comment: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class AuditTrailIsAppendOnly(Exception):
    """Raised on any attempt to update or delete an audit event."""


_APPEND_ONLY = "audit_events is append-only; rows cannot be changed or deleted"


@event.listens_for(AuditEventRow, "before_update")
@event.listens_for(AuditEventRow, "before_delete")
def _block_audit_row_change(mapper, connection, target) -> None:
    raise AuditTrailIsAppendOnly(_APPEND_ONLY)


@event.listens_for(Session, "do_orm_execute")
def _block_audit_bulk_change(state) -> None:
    """Also blocks bulk UPDATE and DELETE statements issued through a session.

    This guards the application. In deployment the database user should additionally be
    granted INSERT and SELECT only on this table.
    """
    if not (state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name == AuditEventRow.__tablename__:
        raise AuditTrailIsAppendOnly(_APPEND_ONLY)
