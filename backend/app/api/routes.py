"""HTTP endpoints. They authenticate, validate the request, call a service and shape the
response. Business rules live in the services (app/services); nothing here changes
reference data.
"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import queries, serializers
from app.api.deps import (
    REVIEWERS,
    AppState,
    current_user,
    get_invoice_service,
    get_session,
    get_state,
    process_in_background,
    require_roles,
)
from app.api.errors import ApiError
from app.domain.enums import InvoiceStatus, Role
from app.extraction.render import SUPPORTED_EXTENSIONS
from app.services.common import User
from app.services.invoices import InvoiceService

router = APIRouter(prefix="/api")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class CorrectionRequest(BaseModel):
    corrections: dict
    comment: str = ""


class DecisionRequest(BaseModel):
    decision: str
    comment: str = ""


class ParametersRequest(BaseModel):
    values: dict
    comment: str = ""


def _is_reviewer(user: User) -> bool:
    return user.role in REVIEWERS


def _reviewer_detail(
    service: InvoiceService, session: Session, invoice_id: int, user: User
) -> dict:
    invoice = service.get_invoice(invoice_id)
    return serializers.invoice_for_reviewer(
        invoice,
        queries.extraction_versions(session, invoice_id),
        service.latest_evaluation(invoice_id),
        service.decisions(invoice_id),
        service.allowed_actions(invoice_id, user),
        queries.evaluations(session, invoice_id),
        queries.supplier_names(session),
    )


# ---- identity


@router.get("/me")
def me(user: User = Depends(current_user), state: AppState = Depends(get_state)) -> dict:
    """Who the caller is, and the upload limits a client should show before uploading."""
    return {
        "username": user.username,
        "role": user.role.value,
        "upload": {
            "max_mb": state.settings.max_upload_mb,
            "extensions": list(SUPPORTED_EXTENSIONS),
        },
    }


# ---- invoices


@router.post("/invoices", status_code=202)
def upload_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    service: InvoiceService = Depends(get_invoice_service),
    state: AppState = Depends(get_state),
) -> dict:
    """Store the document and register it. Extraction and evaluation run in the background."""
    limit_mb = state.settings.max_upload_mb
    max_bytes = limit_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", f"The file is larger than the {limit_mb} MB limit.")
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ApiError(
            422,
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"File type '{extension or 'none'}' is not supported. Upload a PDF, PNG or JPG.",
        )
    if not data:
        raise ApiError(422, "VALIDATION_ERROR", "The file is empty.")

    invoice = service.submit_invoice(data, filename, user)
    background_tasks.add_task(process_in_background, state, invoice.id)
    return {"id": invoice.id, "filename": invoice.filename, "status": invoice.status}


@router.get("/invoices")
def list_invoices(
    status: str | None = None,
    user: User = Depends(current_user),
    service: InvoiceService = Depends(get_invoice_service),
    session: Session = Depends(get_session),
) -> dict:
    if status is not None and status not in {s.value for s in InvoiceStatus}:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"Unknown status '{status}'. Use one of: {', '.join(s.value for s in InvoiceStatus)}.",
        )
    invoices = queries.list_invoices(session, status)
    if _is_reviewer(user):
        names = queries.supplier_names(session)
        return {"invoices": [serializers.invoice_summary(i, names) for i in invoices]}
    return {
        "invoices": [
            serializers.invoice_for_intake(i, service.latest_evaluation(i.id)) for i in invoices
        ]
    }


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    user: User = Depends(current_user),
    service: InvoiceService = Depends(get_invoice_service),
    session: Session = Depends(get_session),
) -> dict:
    if _is_reviewer(user):
        return _reviewer_detail(service, session, invoice_id, user)
    invoice = service.get_invoice(invoice_id)
    return serializers.invoice_for_intake(invoice, service.latest_evaluation(invoice_id))


@router.get("/invoices/{invoice_id}/document")
def get_document(
    invoice_id: int,
    user: User = Depends(current_user),
    service: InvoiceService = Depends(get_invoice_service),
    state: AppState = Depends(get_state),
) -> Response:
    invoice = service.get_invoice(invoice_id)
    try:
        content = state.file_store.load(invoice.stored_path)
    except OSError:
        raise ApiError(404, "NOT_FOUND", "The stored document could not be found.") from None
    media_type = _MEDIA_TYPES.get(Path(invoice.filename).suffix.lower(), "application/octet-stream")
    safe_name = invoice.filename.replace('"', "")
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.get("/invoices/{invoice_id}/audit")
def get_audit_trail(
    invoice_id: int,
    user: User = Depends(require_roles(Role.AP_MANAGER)),
    service: InvoiceService = Depends(get_invoice_service),
) -> dict:
    service.get_invoice(invoice_id)
    return {"events": [serializers.audit_event(e) for e in service.audit_trail(invoice_id)]}


@router.post("/invoices/{invoice_id}/retry", status_code=202)
def retry_extraction(
    invoice_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_roles(*REVIEWERS)),
    service: InvoiceService = Depends(get_invoice_service),
    state: AppState = Depends(get_state),
) -> dict:
    invoice = service.retry_extraction(invoice_id, user)
    background_tasks.add_task(process_in_background, state, invoice.id)
    return {"id": invoice.id, "filename": invoice.filename, "status": invoice.status}


@router.post("/invoices/{invoice_id}/corrections")
def correct_extraction(
    invoice_id: int,
    body: CorrectionRequest,
    user: User = Depends(require_roles(*REVIEWERS)),
    service: InvoiceService = Depends(get_invoice_service),
    session: Session = Depends(get_session),
) -> dict:
    service.correct_extraction(invoice_id, body.corrections, body.comment, user)
    return _reviewer_detail(service, session, invoice_id, user)


@router.post("/invoices/{invoice_id}/decisions")
def decide(
    invoice_id: int,
    body: DecisionRequest,
    # No role filter here: the service refuses and writes the attempt to the audit trail.
    user: User = Depends(current_user),
    service: InvoiceService = Depends(get_invoice_service),
    session: Session = Depends(get_session),
) -> dict:
    service.decide(invoice_id, body.decision, body.comment, user)
    return _reviewer_detail(service, session, invoice_id, user)


@router.get("/review-queue")
def review_queue(
    user: User = Depends(require_roles(*REVIEWERS)),
    session: Session = Depends(get_session),
) -> dict:
    names = queries.supplier_names(session)
    return {"invoices": [serializers.invoice_summary(i, names) for i in queries.review_queue(session)]}


# ---- parameters


@router.get("/parameters")
def get_parameters(
    user: User = Depends(require_roles(*REVIEWERS)),
    service: InvoiceService = Depends(get_invoice_service),
) -> dict:
    return serializers.parameters(service.current_parameters())


@router.put("/parameters")
def update_parameters(
    body: ParametersRequest,
    user: User = Depends(require_roles(Role.AP_MANAGER)),
    service: InvoiceService = Depends(get_invoice_service),
) -> dict:
    return serializers.parameters(service.update_parameters(body.values, body.comment, user))


@router.get("/parameters/history")
def get_parameter_history(
    user: User = Depends(require_roles(Role.AP_MANAGER)),
    session: Session = Depends(get_session),
) -> dict:
    return {"versions": [serializers.parameter_version(r) for r in queries.parameter_history(session)]}
