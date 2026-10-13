"""Database fixtures for matching and service tests. All data is fictional.

Reference data seeded by seed_reference:

  SUP-001 "Test Supplier A", tax id TAX-A, USD, active
    PO-2026-0001 (open, 2026-01-10)
      line 1  ITEM-1  "Widget"         10 ordered @ 5.50   received 6 + 4 = 10 (two GRNs)
      line 2  ITEM-2  "Gadget deluxe"   4 ordered @ 20.00  received 4
  SUP-002 "Test Supplier B", tax id TAX-B, EUR, active
    PO-2026-0002 (open, 2026-02-01)
      line 1  ITEM-9  "Synthetic test item"  4 ordered @ 25.00  received 4

The default payload of extraction_builders.make_payload is a clean invoice on PO-2026-0002.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.config import Settings
from app.db.tables import (
    GoodsReceivedNoteLineRow,
    GoodsReceivedNoteRow,
    PurchaseOrderLineRow,
    PurchaseOrderRow,
    ReferenceSourceRow,
    SupplierRow,
)
from app.domain.enums import Role
from app.extraction.service import ExtractionResult, ExtractionStatus, result_from_extraction
from app.services.common import User
from app.services.invoices import InvoiceService
from app.storage.files import LocalFileStore
from extraction_builders import make_extraction, make_payload, read

INTAKE = User("ines.intake", Role.INTAKE)
ANALYST = User("ana.analyst", Role.AP_ANALYST)
MANAGER = User("max.manager", Role.AP_MANAGER)

PO_A = "PO-2026-0001"
PO_B = "PO-2026-0002"


def seed_reference(session) -> None:
    session.add_all(
        [
            SupplierRow(supplier_id="SUP-001", legal_name="Test Supplier A", tax_id="TAX-A",
                        default_currency="USD", status="active"),
            SupplierRow(supplier_id="SUP-002", legal_name="Test Supplier B", tax_id="TAX-B",
                        default_currency="EUR", status="active"),
        ]
    )
    session.flush()
    session.add_all(
        [
            PurchaseOrderRow(
                po_number=PO_A, supplier_id="SUP-001", po_date=date(2026, 1, 10), currency="USD",
                status="open",
                lines=[
                    PurchaseOrderLineRow(line_number=1, item_code="ITEM-1", description="Widget",
                                         quantity_ordered=Decimal("10"), unit_price=Decimal("5.50")),
                    PurchaseOrderLineRow(line_number=2, item_code="ITEM-2", description="Gadget deluxe",
                                         quantity_ordered=Decimal("4"), unit_price=Decimal("20.00")),
                ],
            ),
            PurchaseOrderRow(
                po_number=PO_B, supplier_id="SUP-002", po_date=date(2026, 2, 1), currency="EUR",
                status="open",
                lines=[
                    PurchaseOrderLineRow(line_number=1, item_code="ITEM-9",
                                         description="Synthetic test item",
                                         quantity_ordered=Decimal("4"), unit_price=Decimal("25.00")),
                ],
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            GoodsReceivedNoteRow(
                grn_number="GRN-1", po_number=PO_A, received_date=date(2026, 1, 15),
                lines=[GoodsReceivedNoteLineRow(po_line_number=1, quantity_received=Decimal("6")),
                       GoodsReceivedNoteLineRow(po_line_number=2, quantity_received=Decimal("4"))],
            ),
            GoodsReceivedNoteRow(
                grn_number="GRN-2", po_number=PO_A, received_date=date(2026, 1, 18),
                lines=[GoodsReceivedNoteLineRow(po_line_number=1, quantity_received=Decimal("4"))],
            ),
            GoodsReceivedNoteRow(
                grn_number="GRN-3", po_number=PO_B, received_date=date(2026, 2, 10),
                lines=[GoodsReceivedNoteLineRow(po_line_number=1, quantity_received=Decimal("4"))],
            ),
            ReferenceSourceRow(file_name="suppliers.json", sha256="a" * 64, record_count=2,
                               loaded_at=datetime(2026, 1, 1)),
        ]
    )
    session.commit()


def widget_payload(quantity="6", unit_price="5.50", invoice_number="A-0001", **overrides) -> dict:
    """An invoice from Test Supplier A for Widgets on PO-2026-0001 line 1 (10 received)."""
    total = str(Decimal(quantity) * Decimal(unit_price))
    header = {
        "supplier_name": "Test Supplier A",
        "supplier_tax_id": "TAX-A",
        "invoice_number": read(invoice_number),
        "invoice_date": read("2026-01-20"),
        "due_date": None,
        "po_references": {"values": [PO_A], "status": "read", "suggested_values": []},
        "currency": "USD",
        "subtotal": total,
        "tax_amount": "0.00",
        "total_amount": total,
    }
    header.update(overrides.pop("header", {}))
    lines = overrides.pop(
        "lines",
        [
            {
                "description": "Widget",
                "quantity": read(quantity),
                "unit_price": read(unit_price),
                "line_total": total,
                "po_line_reference": None,
            }
        ],
    )
    return make_payload(header=header, lines=lines, **overrides)


def extraction_result(payload: dict | None = None) -> ExtractionResult:
    """What the extraction service returns for a successful model call."""
    return result_from_extraction(
        make_extraction(**payload) if payload is not None else make_extraction(),
        model="test-deployment",
        raw_response={"id": "resp_test"},
        tokens={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def failed_result(message="The model service returned HTTP 503.") -> ExtractionResult:
    return ExtractionResult(
        status=ExtractionStatus.EXTRACTION_FAILED,
        errors=[message],
        error_code="MODEL_UNAVAILABLE",
        model="test-deployment",
    )


class StubExtractor:
    """Returns queued results in order. No network, no model."""

    def __init__(self) -> None:
        self.queue: list[ExtractionResult] = []
        self.calls: list[str] = []

    def __call__(self, file_bytes: bytes, filename: str) -> ExtractionResult:
        self.calls.append(filename)
        return self.queue.pop(0)


class TickingClock:
    """Each call is one minute after the previous one, so events have a clear order."""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(minutes=1)
        return self._now


class Harness:
    """A service wired to an in-memory database, a temp folder and a stub extractor."""

    def __init__(self, session, tmp_path) -> None:
        self.session = session
        self.extractor = StubExtractor()
        self.uploads = tmp_path / "uploads"
        self.service = InvoiceService(
            session,
            file_store=LocalFileStore(self.uploads),
            extractor=self.extractor,
            settings=Settings(_env_file=None),
            clock=TickingClock(),
        )

    def submit(self, payload: dict | None = None, *, result=None, filename="invoice.pdf", user=INTAKE):
        """Submit and process one document whose extraction gives the given payload."""
        self.extractor.queue.append(result or extraction_result(payload))
        content = f"file for {filename} #{len(self.extractor.calls)}".encode()
        invoice = self.service.submit_invoice(content, filename, user)
        return self.service.process_invoice(invoice.id)

    def events(self, invoice_id: int) -> list[str]:
        return [event.event_type for event in self.service.audit_trail(invoice_id)]
