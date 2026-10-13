"""Shared golden-set plumbing for the golden test and the manual evaluation script.

Documents are evaluated through the production path: reference data is loaded into an
in-memory database with the real loader, and each document goes through the matching
layer and the rules engine exactly as an uploaded invoice does. Documents are processed
in golden-set order, so duplicates (DL-25) and approved quantities (DL-21) come from the
database, not from test helpers.

Real inputs live under data/, which is git-ignored:
  - data/reference/*.json
  - data/test-invoices/extracted/INV-XX.extracted.json (hand-verified extractions)
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.invoice_tables import ExtractionVersionRow, InvoiceRow
from app.db.session import init_db, make_engine, make_session_factory
from app.domain.enums import ExtractionSource, InvoiceStatus
from app.domain.identifiers import normalize_tax_id
from app.domain.models import ExtractedInvoice
from app.config import Settings
from app.domain.enums import Role
from app.extraction.mapper import MappedInvoice
from app.extraction.schema import ModelExtraction
from app.extraction.service import result_from_extraction
from app.matching.matcher import MatchResult
from app.reference.loader import GRNS_FILE, PURCHASE_ORDERS_FILE, SUPPLIERS_FILE, load_reference
from app.rules.engine import Decision
from app.rules.parameters import RuleParameters
from app.services.common import User
from app.services.evaluation import evaluate_and_store
from app.services.invoices import InvoiceService

REPO = Path(__file__).resolve().parents[2]
REFERENCE_DIR = REPO / "data" / "reference"
INVOICES_DIR = REPO / "data" / "test-invoices"
EXTRACTIONS_DIR = Path(os.environ.get("GOLDEN_EXTRACTIONS_DIR", INVOICES_DIR / "extracted"))
GOLDEN_FILE = Path(__file__).parent / "golden" / "expected_outcomes.json"
GOLDEN = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
EVALUATED_AT = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def verified_extraction_path(filename: str) -> Path:
    return EXTRACTIONS_DIR / f"{Path(filename).stem}.extracted.json"


def mapped_from_verified(raw: dict) -> MappedInvoice:
    """A hand-verified extraction file as engine input.

    The file uses ExtractedInvoice field names; each line also carries po_line_reference,
    the PO line the reviewer read on the document.
    """
    invoice = ExtractedInvoice.model_validate(raw)
    return MappedInvoice(
        invoice=invoice,
        po_references=[invoice.po_number] if invoice.po_number else [],
        line_po_references=[
            int(line["po_line_reference"]) if line.get("po_line_reference") is not None else None
            for line in raw["lines"]
        ],
        supplier_tax_id_normalized=normalize_tax_id(invoice.supplier_tax_id),
    )


def extraction_from_verified_non_invoice(raw: dict) -> ModelExtraction:
    """A hand-verified file for a document that is not an invoice, as a model answer."""

    def field(value) -> dict:
        status = "read" if value is not None else "absent"
        return {"value": value, "status": status, "suggested_value": None}

    return ModelExtraction.model_validate(
        {
            "document_type": raw["document_type"],
            "classification_reason": raw.get("classification_reason") or "Not an invoice.",
            "header": {
                "supplier_name": raw.get("supplier_name"),
                "supplier_tax_id": raw.get("supplier_tax_id"),
                "invoice_number": field(raw.get("invoice_number")),
                "invoice_date": field(raw.get("invoice_date")),
                "due_date": raw.get("due_date"),
                "po_references": {"values": [], "status": "absent", "suggested_values": []},
                "currency": raw.get("currency"),
                "subtotal": raw.get("subtotal"),
                "tax_amount": raw.get("tax_amount"),
                "total_amount": raw.get("total"),
            },
            "lines": [],
        }
    )


class _MemoryFileStore:
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def save(self, sha256: str, extension: str, data: bytes) -> str:
        self._files[sha256 + extension] = data
        return sha256 + extension

    def load(self, stored_path: str) -> bytes:
        return self._files[stored_path]


class GoldenRunner:
    """Evaluates documents one after another against a database holding the reference data."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.parameters = RuleParameters()

    @classmethod
    def load(cls) -> "GoldenRunner | None":
        """Runner over the real reference data, or None when the files are not present."""
        names = (SUPPLIERS_FILE, PURCHASE_ORDERS_FILE, GRNS_FILE)
        if not all((REFERENCE_DIR / name).is_file() for name in names):
            return None
        engine = make_engine("sqlite://")
        init_db(engine)
        session = make_session_factory(engine)()
        load_reference(REFERENCE_DIR, session)
        return cls(session)

    def submit_document(self, extraction: ModelExtraction, filename: str) -> InvoiceRow:
        """Send a document through the invoice service: submit, then extract and evaluate.

        The extraction step returns the given model answer instead of calling the model.
        """
        service = InvoiceService(
            self.session,
            file_store=_MemoryFileStore(),
            extractor=lambda file_bytes, name: result_from_extraction(extraction),
            settings=Settings(_env_file=None),
            clock=lambda: EVALUATED_AT,
        )
        invoice = service.submit_invoice(filename.encode(), filename, User("golden", Role.INTAKE))
        return service.process_invoice(invoice.id)

    def evaluate(
        self, mapped: MappedInvoice, filename: str, payload: dict | None = None
    ) -> tuple[Decision, MatchResult]:
        """Register the document as an invoice, match it, run the engine and store it."""
        invoice = InvoiceRow(
            filename=filename,
            file_sha256="",
            stored_path="",
            uploaded_by="golden",
            uploaded_at=EVALUATED_AT,
            status=InvoiceStatus.PENDING_EXTRACTION.value,
        )
        self.session.add(invoice)
        self.session.flush()
        version = ExtractionVersionRow(
            invoice_id=invoice.id,
            version=1,
            source=ExtractionSource.HUMAN_CORRECTION.value,
            payload=payload if payload is not None else mapped.invoice.model_dump(mode="json"),
            created_by="golden",
            created_at=EVALUATED_AT,
        )
        self.session.add(version)
        self.session.flush()
        decision, match, _ = evaluate_and_store(
            self.session, invoice, version, mapped, self.parameters, EVALUATED_AT
        )
        self.session.commit()
        return decision, match
