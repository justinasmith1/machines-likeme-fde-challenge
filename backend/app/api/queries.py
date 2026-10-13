"""Read-only queries for the API. No business rules and no writes: those are in services."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.invoice_tables import ExtractionVersionRow, InvoiceRow, RuleParametersRow
from app.domain.enums import InvoiceStatus


def list_invoices(session: Session, status: str | None = None) -> list[InvoiceRow]:
    statement = select(InvoiceRow).order_by(InvoiceRow.id.desc())
    if status is not None:
        statement = statement.where(InvoiceRow.status == status)
    return list(session.scalars(statement))


def review_queue(session: Session) -> list[InvoiceRow]:
    """Invoices waiting for a person, oldest first.

    HELD_FOR_REVIEW always means there is no decision in force: a decision moves the
    invoice to another status, and a correction supersedes the decision it follows.
    """
    return list(
        session.scalars(
            select(InvoiceRow)
            .where(InvoiceRow.status == InvoiceStatus.HELD_FOR_REVIEW.value)
            .order_by(InvoiceRow.uploaded_at, InvoiceRow.id)
        )
    )


def extraction_versions(session: Session, invoice_id: int) -> list[ExtractionVersionRow]:
    return list(
        session.scalars(
            select(ExtractionVersionRow)
            .where(ExtractionVersionRow.invoice_id == invoice_id)
            .order_by(ExtractionVersionRow.version)
        )
    )


def parameter_history(session: Session) -> list[RuleParametersRow]:
    return list(
        session.scalars(select(RuleParametersRow).order_by(RuleParametersRow.version.desc()))
    )
