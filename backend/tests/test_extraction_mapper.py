"""Mapping of model output to engine input. Whether the engine runs at all is decided by
the invoice service (see test_services)."""

from datetime import date
from types import SimpleNamespace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.enums import InvoiceStatus, Outcome
from app.extraction.mapper import MULTIPLE_PO_REFERENCES, to_engine_input
from app.reference.schemas import PurchaseOrder, PurchaseOrderLine, Supplier
from app.rules.engine import MatchingContext, evaluate
from app.rules.parameters import RuleParameters
from extraction_builders import make_extraction, make_payload, not_read, read
from rules_builders import NOW

P = RuleParameters()
PO_NUMBER = "PO-2026-0002"


def _line(**overrides) -> dict:
    line = make_payload()["lines"][0]
    line.update(overrides)
    return line


def _context(mapped) -> MatchingContext:
    """Context in which the default synthetic invoice is a clean AUTO_PAY."""
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
    supplier = Supplier(
        supplier_id="SUP-002",
        legal_name="Test Supplier B",
        tax_id="TAX-B",
        default_currency="EUR",
        status="active",
    )
    references = mapped.po_references
    return MatchingContext(
        supplier=supplier,
        purchase_orders={PO_NUMBER: po} if PO_NUMBER in references else {},
        missing_po_numbers=[r for r in references[:1] if r != PO_NUMBER],
        received_by_po_line={(PO_NUMBER, 1): Decimal("4")},
        already_invoiced_by_po_line={},
        prior_invoice_keys=set(),
        line_mapping={
            index + 1: (mapped.invoice.po_number, reference)
            if mapped.invoice.po_number and reference is not None
            else None
            for index, reference in enumerate(mapped.line_po_references)
        },
        evaluated_at=NOW,
    )


def _decide(**overrides):
    """Map a model answer and run the engine on it, as the invoice service does."""
    mapped = to_engine_input(make_extraction(**overrides))
    decision = evaluate(mapped.invoice, _context(mapped), P)
    return SimpleNamespace(
        status=InvoiceStatus[decision.outcome.name], decision=decision, flags=list(mapped.flags)
    )


def _codes(outcome) -> list[str]:
    return [r.code for r in outcome.decision.reasons]


def test_clean_invoice_maps_every_field():
    mapped = to_engine_input(make_extraction())
    invoice = mapped.invoice
    assert invoice.supplier_name == "Test Supplier B"
    assert invoice.supplier_tax_id == "TAX-B"
    assert invoice.invoice_number == "TEST-0001"
    assert invoice.invoice_date == date(2026, 2, 15)
    assert invoice.po_number == PO_NUMBER and invoice.po_number_illegible is False
    assert invoice.currency == "EUR"
    assert (invoice.subtotal, invoice.tax_amount, invoice.total) == (
        Decimal("100.00"),
        Decimal("21.00"),
        Decimal("121.00"),
    )
    line = invoice.lines[0]
    assert (line.quantity, line.unit_price, line.line_total) == (
        Decimal("4"),
        Decimal("25.00"),
        Decimal("100.00"),
    )
    assert mapped.po_references == [PO_NUMBER]
    assert mapped.line_po_references == [1]
    assert mapped.flags == []


def test_clean_invoice_reaches_the_engine_and_is_auto_pay():
    outcome = _decide()
    assert outcome.status is InvoiceStatus.AUTO_PAY
    assert outcome.decision.outcome is Outcome.AUTO_PAY and outcome.decision.reasons == []


def test_illegible_quantity_becomes_none_and_holds_with_a6():
    lines = [_line(quantity=not_read("illegible", suggested="4"))]
    assert to_engine_input(make_extraction(lines=lines)).invoice.lines[0].quantity is None
    outcome = _decide(lines=lines)
    assert _codes(outcome) == ["A6"]
    assert outcome.status is InvoiceStatus.HELD_FOR_REVIEW


def test_illegible_unit_price_becomes_none_and_holds_with_a6():
    lines = [_line(unit_price=not_read("illegible"))]
    assert to_engine_input(make_extraction(lines=lines)).invoice.lines[0].unit_price is None
    assert _codes(_decide(lines=lines)) == ["A6"]


def test_illegible_po_sets_the_flag_and_holds_with_a7_not_r1():
    header = {"po_references": {"values": [], "status": "illegible", "suggested_values": [PO_NUMBER]}}
    mapped = to_engine_input(make_extraction(header=header))
    assert mapped.invoice.po_number is None
    assert mapped.invoice.po_number_illegible is True
    assert mapped.po_references == []

    outcome = _decide(header=header)
    assert "A7" in _codes(outcome) and "R1" not in _codes(outcome)
    assert outcome.status is InvoiceStatus.HELD_FOR_REVIEW


def test_absent_po_is_rejected_with_r1():
    header = {"po_references": {"values": [], "status": "absent", "suggested_values": []}}
    mapped = to_engine_input(make_extraction(header=header))
    assert mapped.invoice.po_number is None
    assert mapped.invoice.po_number_illegible is False

    outcome = _decide(header=header)
    assert "R1" in _codes(outcome) and "A7" not in _codes(outcome)
    assert outcome.status is InvoiceStatus.REJECTED


@pytest.mark.parametrize("status", ["illegible", "absent"])
def test_missing_invoice_number_becomes_none_and_holds_with_a9(status):
    header = {"invoice_number": not_read(status)}
    invoice = to_engine_input(make_extraction(header=header)).invoice
    assert invoice.invoice_number is None
    assert invoice.invoice_number_illegible is (status == "illegible")
    assert invoice.invoice_date_illegible is False
    outcome = _decide(header=header)
    assert _codes(outcome) == ["A9"]
    assert outcome.status is InvoiceStatus.HELD_FOR_REVIEW


@pytest.mark.parametrize("status", ["illegible", "absent"])
def test_missing_invoice_date_becomes_none_and_holds_with_a9(status):
    header = {"invoice_date": not_read(status)}
    invoice = to_engine_input(make_extraction(header=header)).invoice
    assert invoice.invoice_date is None
    assert invoice.invoice_date_illegible is (status == "illegible")
    assert invoice.invoice_number_illegible is False
    assert _codes(_decide(header=header)) == ["A9"]


def test_fields_that_were_read_are_not_marked_illegible():
    invoice = to_engine_input(make_extraction()).invoice
    assert invoice.invoice_number and invoice.invoice_date
    assert (invoice.invoice_number_illegible, invoice.invoice_date_illegible) == (False, False)


def test_suggested_values_never_reach_the_engine():
    extraction = make_extraction(
        header={
            "invoice_number": not_read("illegible", suggested="TEST-0001"),
            "invoice_date": not_read("illegible", suggested="2026-02-15"),
            "po_references": {"values": [], "status": "illegible", "suggested_values": [PO_NUMBER]},
        },
        lines=[
            _line(
                quantity=not_read("illegible", suggested="4"),
                unit_price=not_read("illegible", suggested="25.00"),
            )
        ],
    )
    # The suggestions are kept on the extraction for the analyst...
    assert extraction.lines[0].quantity.suggested_value == Decimal("4")
    assert extraction.header.po_references.suggested_values == [PO_NUMBER]
    # ...and none of them is in what the engine receives.
    invoice = to_engine_input(extraction).invoice
    assert invoice.invoice_number is None and invoice.invoice_date is None
    assert invoice.po_number is None
    assert invoice.lines[0].quantity is None and invoice.lines[0].unit_price is None


def test_value_given_for_an_illegible_field_is_demoted_to_a_suggestion():
    # The model breaks the contract and puts a guess in `value`. It must not be used.
    lines = [_line(quantity={"value": "4", "status": "illegible", "suggested_value": None})]
    extraction = make_extraction(lines=lines)
    assert extraction.lines[0].quantity.value is None
    assert extraction.lines[0].quantity.suggested_value == Decimal("4")
    assert to_engine_input(extraction).invoice.lines[0].quantity is None


def test_read_status_without_a_value_is_invalid():
    with pytest.raises(ValidationError, match="status is 'read' but value is null"):
        make_extraction(lines=[_line(quantity={"value": None, "status": "read", "suggested_value": None})])


def test_amounts_must_be_decimal_strings():
    with pytest.raises(ValidationError):
        make_extraction(header={"total_amount": "1,210.00"})


def test_multiple_po_references_map_the_first_and_add_a_visible_flag():
    header = {
        "po_references": {
            "values": [PO_NUMBER, "PO-2026-0003"],
            "status": "read",
            "suggested_values": [],
        }
    }
    mapped = to_engine_input(make_extraction(header=header))
    assert mapped.invoice.po_number == PO_NUMBER
    assert mapped.po_references == [PO_NUMBER, "PO-2026-0003"]
    assert mapped.flags == [MULTIPLE_PO_REFERENCES]
    assert "PO-2026-0003" in mapped.notes[0]
    assert MULTIPLE_PO_REFERENCES in _decide(header=header).flags
