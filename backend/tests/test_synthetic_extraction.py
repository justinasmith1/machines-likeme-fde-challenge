"""Always-on check: a fictional extraction file evaluated end to end, with no data/ needed."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.domain.enums import Outcome
from app.domain.models import ExtractedInvoice
from app.reference.schemas import PurchaseOrder, PurchaseOrderLine, Supplier
from app.rules.engine import MatchingContext, evaluate
from app.rules.parameters import RuleParameters
from rules_builders import NOW

FIXTURE = Path(__file__).parent / "fixtures" / "extraction" / "synthetic_invoice.json"
PO_NUMBER = "PO-2026-0002"


def test_synthetic_invoice_is_auto_pay():
    invoice = ExtractedInvoice.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    supplier = Supplier(
        supplier_id="SUP-002",
        legal_name="Test Supplier B",
        tax_id="TAX-B",
        default_currency="EUR",
        status="active",
    )
    po = PurchaseOrder(
        po_number=PO_NUMBER,
        supplier_id="SUP-002",
        po_date=date(2026, 2, 1),
        currency="EUR",
        status="open",
        lines=[
            PurchaseOrderLine(
                line_number=1,
                item_code="ITEM-9",
                description="Synthetic test item",
                quantity_ordered=Decimal("4"),
                unit_price=Decimal("25.00"),
            )
        ],
    )
    context = MatchingContext(
        supplier=supplier,
        purchase_orders={PO_NUMBER: po},
        missing_po_numbers=[],
        received_by_po_line={(PO_NUMBER, 1): Decimal("4")},
        already_invoiced_by_po_line={},
        prior_invoice_keys=set(),
        line_mapping={1: (PO_NUMBER, 1)},
        evaluated_at=NOW,
    )

    decision = evaluate(invoice, context, RuleParameters())

    assert decision.outcome is Outcome.AUTO_PAY
    assert decision.reasons == []
