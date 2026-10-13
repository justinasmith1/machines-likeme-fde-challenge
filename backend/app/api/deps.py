"""FastAPI dependencies: database session, the invoice service, and the current user.

Authentication is behind one dependency, current_user. In development it reads the user
and role from the X-Dev-User and X-Dev-Role headers. In any other environment those
headers are ignored and every request is refused with 401 until phase 6 replaces this
dependency with Microsoft Entra ID. Endpoints depend only on current_user / require_roles,
so they do not change when that happens.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError, unauthenticated
from app.config import Settings
from app.domain.enums import Role
from app.services.common import User
from app.services.invoices import Extractor, InvoiceService
from app.storage.files import FileStore

logger = logging.getLogger(__name__)

DEVELOPMENT = "development"


@dataclass
class AppState:
    """What the app was built with. Tests replace the extractor, the store and the database."""

    settings: Settings
    session_factory: sessionmaker[Session]
    file_store: FileStore
    extractor: Extractor


def get_state(request: Request) -> AppState:
    return request.app.state.container


def get_session(state: AppState = Depends(get_state)) -> Iterator[Session]:
    session = state.session_factory()
    try:
        yield session
    finally:
        session.close()


def build_service(state: AppState, session: Session) -> InvoiceService:
    return InvoiceService(
        session,
        file_store=state.file_store,
        extractor=state.extractor,
        settings=state.settings,
    )


def get_invoice_service(
    state: AppState = Depends(get_state), session: Session = Depends(get_session)
) -> InvoiceService:
    return build_service(state, session)


def process_in_background(state: AppState, invoice_id: int) -> None:
    """Extract and evaluate an invoice after the response was sent.

    Uses its own database session: the request's session is closed by then.
    """
    session = state.session_factory()
    try:
        build_service(state, session).process_invoice(invoice_id)
    except Exception:
        # The service already turns processing errors into EXTRACTION_FAILED. This only
        # catches what happens outside it (for example the invoice no longer pending).
        session.rollback()
        logger.exception("Background processing of invoice %s failed", invoice_id)
    finally:
        session.close()


def current_user(request: Request, state: AppState = Depends(get_state)) -> User:
    if state.settings.app_env != DEVELOPMENT:
        # Development headers are never honoured outside development.
        raise unauthenticated("Authentication is not configured for this environment.")
    username = (request.headers.get("X-Dev-User") or "").strip()
    role = (request.headers.get("X-Dev-Role") or "").strip().upper()
    if not username or not role:
        raise unauthenticated("Send the X-Dev-User and X-Dev-Role headers (development only).")
    try:
        return User(username=username, role=Role(role))
    except ValueError:
        raise unauthenticated(
            f"Unknown role '{role}'. Use one of: {', '.join(r.value for r in Role)}."
        ) from None


def require_roles(*roles: Role) -> Callable[[User], User]:
    """Dependency that lets only the given roles through. The services check again."""

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            allowed = " or ".join(r.value for r in roles)
            raise ApiError(403, "PERMISSION_DENIED", f"This action requires the role {allowed}.")
        return user

    return dependency


REVIEWERS = (Role.AP_ANALYST, Role.AP_MANAGER)
