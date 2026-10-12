"""Builders for rule tests. The default invoice produces AUTO_PAY with no reasons.

Each test changes one thing through keyword arguments, so the neighbour case
(the one that must NOT trigger the rule) is the default.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.enums import Outcome
from app.domain.models import ExtractedInvoice, ExtractedInvoiceLine
from app.reference.schemas import GoodsReceivedNote, PurchaseOrder, PurchaseOrderLine, Supplier
from app.rules.engine import MatchingContext

PO = "PO-2026-0001"
NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def make_supplier(**overrides) -> Supplier:
    data = dict(
        supplier_id="SUP-001",
        legal_name="Test Supplier A",
        tax_id="TAX-A",
        default_currency="USD",
        status="active",
    )
    data.update(overrides)
    return Supplier(**data)


def make_line(line_number=1, quantity_ordered="10", unit_price="5.50") -> PurchaseOrderLine:
    return PurchaseOrderLine(
        line_number=line_number,
        item_code="ITEM-1",
        description="Widget",
        quantity_ordered=Decimal(quantity_ordered),
        unit_price=Decimal(unit_price),
    )


def make_po(lines: list[PurchaseOrderLine] | None = None, **overrides) -> PurchaseOrder:
    data = dict(
        po_number=PO,
        supplier_id="SUP-001",
        po_date=date(2026, 1, 10),
        currency="USD",
        status="open",
        lines=lines if lines is not None else [make_line()],
    )
    data.update(overrides)
    return PurchaseOrder(**data)


def make_invoice(
    quantity: str | None = "10",
    unit_price: str | None = "5.50",
    line_total: str | None = None,
    **overrides,
) -> ExtractedInvoice:
    """One-line invoice. Totals are consistent unless overridden."""
    qty = Decimal(quantity) if quantity is not None else None
    price = Decimal(unit_price) if unit_price is not None else None
    if line_total is not None:
        total = Decimal(line_total)
    elif qty is not None and price is not None:
        total = qty * price
    else:
        total = None
    line = ExtractedInvoiceLine(
        item_code="ITEM-1",
        description="Widget",
        quantity=qty,
        unit_price=price,
        line_total=total,
    )
    data = dict(
        supplier_name="Test Supplier A",
        supplier_tax_id="TAX-A",
        invoice_number="INV-0001",
        invoice_date=date(2026, 1, 20),
        po_number=PO,
        currency="USD",
        lines=[line],
        subtotal=total,
        tax_amount=Decimal("0.00") if total is not None else None,
        total=total,
    )
    data.update(overrides)
    return ExtractedInvoice(**data)


def make_context(**overrides) -> MatchingContext:
    data = dict(
        supplier=make_supplier(),
        purchase_orders={PO: make_po()},
        missing_po_numbers=[],
        received_by_po_line={(PO, 1): Decimal("10")},
        already_invoiced_by_po_line={},
        prior_invoice_keys=set(),
        line_mapping={1: (PO, 1)},
        evaluated_at=NOW,
    )
    data.update(overrides)
    return MatchingContext(**data)


def received_by_po_line(grns: list[GoodsReceivedNote]) -> dict[tuple[str, int], Decimal]:
    """Sum every GRN line per (po_number, po_line_number).

    This is the matching layer's job (phase 4). It lives here only so the tests can
    check the summing behaviour.
    """
    totals: dict[tuple[str, int], Decimal] = {}
    for grn in grns:
        for line in grn.lines:
            key = (grn.po_number, line.po_line_number)
            totals[key] = totals.get(key, Decimal("0")) + line.quantity_received
    return totals


def add_approved_quantities(
    already_invoiced: dict[tuple[str, int], Decimal],
    invoice: ExtractedInvoice,
    line_mapping: dict[int, tuple[str, int] | None],
    decision,
) -> None:
    """Accumulate an evaluated invoice into already_invoiced_by_po_line (DL-21).

    Only invoices approved for payment (AUTO_PAY) reserve quantity. Held and rejected
    invoices reserve nothing. This belongs to the matching layer (phase 4); it lives
    here so the tests can exercise the rule.
    """
    if decision.outcome is not Outcome.AUTO_PAY:
        return
    for index, line in enumerate(invoice.lines):
        key = line_mapping.get(index + 1)
        if key is not None and line.quantity is not None:
            already_invoiced[key] = already_invoiced.get(key, Decimal("0")) + line.quantity


def codes(decision) -> list[str]:
    return [r.code for r in decision.reasons]
