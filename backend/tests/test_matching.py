"""Matching layer: supplier resolution, purchase orders, line mapping, duplicates and
quantities already approved, all read from the database."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.enums import InvoiceStatus
from app.extraction.mapper import to_engine_input
from app.matching.matcher import (
    DESCRIPTION,
    ITEM_CODE,
    PRINTED_PO_LINE,
    UNMAPPED,
    build_context,
)
from db_builders import ANALYST, MANAGER, PO_A, PO_B, Harness, seed_reference, widget_payload
from extraction_builders import make_extraction, not_read, read

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(session):
    seed_reference(session)
    return session


@pytest.fixture
def harness(db, tmp_path):
    return Harness(db, tmp_path)


def _match(session, payload: dict, invoice_id=None):
    return build_context(
        to_engine_input(make_extraction(**payload)), session, evaluated_at=NOW, invoice_id=invoice_id
    )


def _line(description, po_line_reference=None, quantity="1", unit_price="5.50") -> dict:
    return {
        "description": description,
        "quantity": read(quantity),
        "unit_price": read(unit_price),
        "line_total": str(Decimal(quantity) * Decimal(unit_price)),
        "po_line_reference": po_line_reference,
    }


# ---- supplier


def test_supplier_is_resolved_by_normalized_tax_id(db):
    match = _match(db, widget_payload(header={"supplier_tax_id": "Tax ID: tax-a", "supplier_name": "Other"}))
    assert match.supplier.supplier_id == "SUP-001"
    assert match.supplier_method == "TAX_ID"
    assert match.context.supplier.supplier_id == "SUP-001"


def test_supplier_falls_back_to_normalized_name_when_tax_id_does_not_match(db):
    match = _match(db, widget_payload(header={"supplier_tax_id": "UNKNOWN-9", "supplier_name": "TEST  SUPPLIER A."}))
    assert match.supplier.supplier_id == "SUP-001"
    assert match.supplier_method == "NAME"


def test_supplier_falls_back_to_name_when_tax_id_is_missing(db):
    match = _match(db, widget_payload(header={"supplier_tax_id": None}))
    assert match.supplier.supplier_id == "SUP-001" and match.supplier_method == "NAME"


def test_supplier_name_must_match_exactly_after_normalization(db):
    match = _match(db, widget_payload(header={"supplier_tax_id": None, "supplier_name": "Test Supplier"}))
    assert match.supplier is None
    assert match.supplier_method == "UNRESOLVED"
    assert match.context.supplier is None  # the engine applies R3


# ---- purchase orders and receipts


def test_existing_and_missing_purchase_orders_are_separated(db):
    header = {"po_references": {"values": [PO_A, "PO-2026-0999"], "status": "read", "suggested_values": []}}
    match = _match(db, widget_payload(header=header))
    assert list(match.context.purchase_orders) == [PO_A]
    assert match.context.missing_po_numbers == ["PO-2026-0999"]
    assert match.po_references == [PO_A, "PO-2026-0999"]


def test_received_quantity_is_the_sum_of_all_grns(db):
    match = _match(db, widget_payload())
    assert match.context.received_by_po_line[(PO_A, 1)] == Decimal("10")  # 6 + 4
    assert match.context.received_by_po_line[(PO_A, 2)] == Decimal("4")
    assert (PO_B, 1) not in match.context.received_by_po_line  # only referenced orders


def test_purchase_order_is_read_from_the_database_with_its_lines(db):
    po = _match(db, widget_payload()).context.purchase_orders[PO_A]
    assert po.supplier_id == "SUP-001" and po.currency == "USD"
    assert [(line.line_number, line.item_code, line.unit_price) for line in po.lines] == [
        (1, "ITEM-1", Decimal("5.50")),
        (2, "ITEM-2", Decimal("20.00")),
    ]


# ---- line mapping (DL-15)


def test_line_maps_by_the_po_line_printed_on_the_document(db):
    match = _match(db, widget_payload(lines=[_line("Anything at all", po_line_reference=2)]))
    assert match.line_matches[0].method == PRINTED_PO_LINE
    assert match.context.line_mapping == {1: (PO_A, 2)}


def test_printed_po_line_that_does_not_exist_falls_through_to_description(db):
    match = _match(db, widget_payload(lines=[_line("widget", po_line_reference=9)]))
    assert match.line_matches[0].method == DESCRIPTION
    assert match.context.line_mapping == {1: (PO_A, 1)}


def test_line_maps_by_identical_normalized_description(db):
    match = _match(db, widget_payload(lines=[_line("  GADGET   deluxe. ")]))
    assert match.line_matches[0].method == DESCRIPTION
    assert match.context.line_mapping == {1: (PO_A, 2)}


def test_line_maps_by_item_code_contained_in_the_description(db):
    match = _match(db, widget_payload(lines=[_line("Spare part ref. item-2 (blue)")]))
    assert match.line_matches[0].method == ITEM_CODE
    assert match.context.line_mapping == {1: (PO_A, 2)}


def test_line_without_a_single_match_stays_unmapped(db):
    match = _match(db, widget_payload(lines=[_line("Consulting hours")]))
    assert match.line_matches[0].method == UNMAPPED
    assert match.context.line_mapping == {1: None}  # the engine applies H3


def test_line_matching_two_po_lines_stays_unmapped(db):
    # Both item codes appear in the description: no single match.
    match = _match(db, widget_payload(lines=[_line("Bundle ITEM-1 + ITEM-2")]))
    assert match.line_matches[0].method == UNMAPPED


def test_each_line_records_its_method_and_quantity(db):
    lines = [_line("Widget", quantity="3"), _line("Unknown thing"), _line("x", po_line_reference=2, quantity="2")]
    match = _match(db, widget_payload(lines=lines))
    assert [(m.line_number, m.po_line_number, m.method, m.quantity) for m in match.line_matches] == [
        (1, 1, DESCRIPTION, Decimal("3")),
        (2, None, UNMAPPED, Decimal("1")),
        (3, 2, PRINTED_PO_LINE, Decimal("2")),
    ]
    stored = match.to_json()
    assert stored["supplier_id"] == "SUP-001"
    assert stored["lines"][0] == {
        "line_number": 1, "po_number": PO_A, "po_line_number": 1, "method": DESCRIPTION, "quantity": "3",
    }


def test_no_lines_are_mapped_when_the_purchase_order_does_not_exist(db):
    header = {"po_references": {"values": ["PO-2026-0999"], "status": "read", "suggested_values": []}}
    match = _match(db, widget_payload(header=header, lines=[_line("Widget", po_line_reference=1)]))
    assert match.context.line_mapping == {1: None}


def test_illegible_quantity_is_kept_as_unknown(db):
    line = _line("Widget")
    line["quantity"] = not_read("illegible", suggested="6")
    match = _match(db, widget_payload(lines=[line]))
    assert match.line_matches[0].quantity is None


# ---- quantities already approved for payment (DL-21, DL-36). Line 1 has 10 received.


def test_auto_pay_invoice_reserves_quantity(harness):
    first = harness.submit(widget_payload(quantity="6", invoice_number="A-1"))
    assert first.status == InvoiceStatus.AUTO_PAY.value
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.HELD_FOR_REVIEW.value
    codes = [r["code"] for r in harness.service.latest_evaluation(second.id).reasons]
    assert codes == ["A8"]


def test_held_invoice_without_a_decision_reserves_nothing(harness):
    first = harness.submit(widget_payload(quantity="6", unit_price="9.00", invoice_number="A-1"))
    assert first.status == InvoiceStatus.HELD_FOR_REVIEW.value  # price beyond tolerance
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.AUTO_PAY.value


def test_held_invoice_approved_by_a_person_reserves_quantity(harness):
    first = harness.submit(widget_payload(quantity="6", unit_price="9.00", invoice_number="A-1"))
    harness.service.decide(first.id, "APPROVE", "Price agreed with purchasing.", ANALYST)
    assert first.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert [r["code"] for r in harness.service.latest_evaluation(second.id).reasons] == ["A8"]


def test_rejected_invoice_reserves_nothing(harness):
    early = widget_payload(quantity="6", invoice_number="A-1", header={"invoice_date": read("2026-01-01")})
    first = harness.submit(early)
    assert first.status == InvoiceStatus.REJECTED.value  # dated before the PO
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.AUTO_PAY.value


def test_rejected_invoice_overridden_by_a_person_reserves_quantity(harness):
    early = widget_payload(quantity="6", invoice_number="A-1", header={"invoice_date": read("2026-01-01")})
    first = harness.submit(early)
    harness.service.decide(first.id, "OVERRIDE", "Date is a typo, confirmed with supplier.", MANAGER)
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.HELD_FOR_REVIEW.value


def test_an_invoice_is_not_counted_against_itself_on_re_evaluation(harness):
    invoice = harness.submit(widget_payload(quantity="10", invoice_number="A-1"))
    assert invoice.status == InvoiceStatus.AUTO_PAY.value
    harness.service.correct_extraction(
        invoice.id, {"header": {"due_date": "2026-02-20"}}, "Added due date.", ANALYST
    )
    evaluation = harness.service.latest_evaluation(invoice.id)
    assert evaluation.outcome == "AUTO_PAY" and evaluation.reasons == []  # its own 10 do not count twice


# ---- duplicates (DL-25)


def test_duplicate_is_detected_against_the_database_whatever_the_first_status(harness):
    first = harness.submit(widget_payload(quantity="6", unit_price="9.00", invoice_number="DUP-1"))
    assert first.status == InvoiceStatus.HELD_FOR_REVIEW.value
    copy = harness.submit(widget_payload(quantity="6", unit_price="9.00", invoice_number="DUP-1"))
    assert copy.status == InvoiceStatus.REJECTED.value
    assert "R4" in [r["code"] for r in harness.service.latest_evaluation(copy.id).reasons]


def test_the_first_copy_is_not_a_duplicate_of_the_later_one(harness):
    first = harness.submit(widget_payload(quantity="2", invoice_number="DUP-1"))
    harness.submit(widget_payload(quantity="2", invoice_number="DUP-1"))
    # Re-evaluating the first one must not see the later copy as "already processed".
    harness.service.correct_extraction(first.id, {"header": {"due_date": "2026-02-20"}}, "Due date.", ANALYST)
    evaluation = harness.service.latest_evaluation(first.id)
    assert evaluation.outcome == "AUTO_PAY" and evaluation.reasons == []


def test_same_number_from_another_supplier_is_not_a_duplicate(harness):
    harness.submit(widget_payload(quantity="2", invoice_number="SAME-1"))
    other = harness.submit({"header": {"invoice_number": read("SAME-1")}})  # Test Supplier B
    assert other.status == InvoiceStatus.AUTO_PAY.value
