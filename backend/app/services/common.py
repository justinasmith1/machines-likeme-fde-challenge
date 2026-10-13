"""Small shared pieces for the services: the acting user, the clock and the audit writer."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.invoice_tables import AuditEventRow
from app.domain.enums import Role

SYSTEM_ACTOR = "system"
SYSTEM_ROLE = "SYSTEM"


@dataclass(frozen=True)
class User:
    username: str
    role: Role


def utc_now() -> datetime:
    return datetime.now(UTC)


def record_event(
    session: Session,
    event_type: str,
    *,
    timestamp: datetime,
    actor: str = SYSTEM_ACTOR,
    role: str = SYSTEM_ROLE,
    invoice_id: int | None = None,
    details: dict | None = None,
) -> AuditEventRow:
    """Append one audit event. Events are never updated or deleted."""
    row = AuditEventRow(
        invoice_id=invoice_id,
        event_type=event_type,
        actor=actor,
        role=str(role),
        timestamp=timestamp,
        details=details or {},
    )
    session.add(row)
    return row
