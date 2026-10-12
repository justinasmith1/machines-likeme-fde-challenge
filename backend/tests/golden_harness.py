"""Shared golden-set plumbing for the golden test and the manual evaluation script.

It stands in for the matching layer (phase 4): it resolves the supplier by normalized
tax id (DL-43), picks
the referenced purchase orders, sums GRNs, and carries state from one document to the
next in golden-set order (duplicates per DL-25, approved quantities per DL-21).

Real inputs live under data/, which is git-ignored:
  - data/reference/*.json
  - data/test-invoices/extracted/INV-XX.extracted.json (hand-verified extractions)
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.domain.identifiers import normalize_tax_id
from app.domain.models import ExtractedInvoice
from app.reference.loader import GRNS_FILE, PURCHASE_ORDERS_FILE, SUPPLIERS_FILE, _read_records
from app.reference.schemas import GoodsReceivedNote, PurchaseOrder, Supplier
from app.rules.engine import Decision, MatchingContext
from rules_builders import add_approved_quantities

REPO = Path(__file__).resolve().parents[2]
REFERENCE_DIR = REPO / "data" / "reference"
INVOICES_DIR = REPO / "data" / "test-invoices"
EXTRACTIONS_DIR = Path(os.environ.get("GOLDEN_EXTRACTIONS_DIR", INVOICES_DIR / "extracted"))
GOLDEN_FILE = Path(__file__).parent / "golden" / "expected_outcomes.json"
GOLDEN = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
EVALUATED_AT = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)

LineMapping = dict[int, tuple[str, int] | None]


def verified_extraction_path(filename: str) -> Path:
    return EXTRACTIONS_DIR / f"{Path(filename).stem}.extracted.json"


def line_mapping(po_number: str | None, line_references: list[int | None]) -> LineMapping:
    """Invoice line number -> (po_number, po_line), from the reference printed per line."""
    return {
        index + 1: (po_number, int(reference))
        if po_number and reference is not None
        else None
        for index, reference in enumerate(line_references)
    }


class GoldenState:
    """Reference data plus what earlier documents left behind."""

    def __init__(self, suppliers_by_tax_id, purchase_orders, received) -> None:
        self.suppliers_by_tax_id: dict[str, Supplier] = suppliers_by_tax_id
        self.purchase_orders: dict[str, PurchaseOrder] = purchase_orders
        self.received: dict[tuple[str, int], Decimal] = received
        self.prior_invoice_keys: set[tuple[str, str]] = set()
        self.already_invoiced: dict[tuple[str, int], Decimal] = {}

    @classmethod
    def load(cls) -> "GoldenState | None":
        """None when the reference files are not present."""
        names = (SUPPLIERS_FILE, PURCHASE_ORDERS_FILE, GRNS_FILE)
        if not all((REFERENCE_DIR / name).is_file() for name in names):
            return None
        errors: list[str] = []
        suppliers_raw, _ = _read_records(REFERENCE_DIR / SUPPLIERS_FILE, errors)
        pos_raw, _ = _read_records(REFERENCE_DIR / PURCHASE_ORDERS_FILE, errors)
        grns_raw, _ = _read_records(REFERENCE_DIR / GRNS_FILE, errors)
        assert not errors, errors
        return cls.from_records(
            [Supplier.model_validate(s) for s in suppliers_raw],
            [PurchaseOrder.model_validate(p) for p in pos_raw],
            [GoodsReceivedNote.model_validate(g) for g in grns_raw],
        )

    @classmethod
    def from_records(
        cls,
        suppliers: list[Supplier],
        purchase_orders: list[PurchaseOrder],
        grns: list[GoodsReceivedNote],
    ) -> "GoldenState":
        received: dict[tuple[str, int], Decimal] = {}
        for grn in grns:
            for line in grn.lines:
                key = (grn.po_number, line.po_line_number)
                received[key] = received.get(key, Decimal("0")) + line.quantity_received
        return cls(
            # Keyed by normalized tax id; the master records themselves are not changed.
            {normalize_tax_id(s.tax_id): s for s in suppliers},
            {p.po_number: p for p in purchase_orders},
            received,
        )

    def supplier_for(self, invoice: ExtractedInvoice) -> Supplier | None:
        return self.suppliers_by_tax_id.get(normalize_tax_id(invoice.supplier_tax_id))

    def build_context(self, invoice: ExtractedInvoice, mapping: LineMapping) -> MatchingContext:
        referenced = [invoice.po_number] if invoice.po_number else []
        return MatchingContext(
            supplier=self.supplier_for(invoice),
            purchase_orders={n: self.purchase_orders[n] for n in referenced if n in self.purchase_orders},
            missing_po_numbers=[n for n in referenced if n not in self.purchase_orders],
            received_by_po_line=dict(self.received),
            already_invoiced_by_po_line=dict(self.already_invoiced),
            prior_invoice_keys=set(self.prior_invoice_keys),
            line_mapping=mapping,
            evaluated_at=EVALUATED_AT,
        )

    def record(self, invoice: ExtractedInvoice, mapping: LineMapping, decision: Decision) -> None:
        """Update the state for the documents that follow."""
        supplier = self.supplier_for(invoice)
        # DL-25: any earlier invoice counts for duplicates, whatever its outcome.
        if supplier is not None and invoice.invoice_number:
            self.prior_invoice_keys.add((supplier.supplier_id, invoice.invoice_number))
        # DL-21: only invoices the engine actually sent to AUTO_PAY reserve quantity.
        add_approved_quantities(self.already_invoiced, invoice, mapping, decision)
