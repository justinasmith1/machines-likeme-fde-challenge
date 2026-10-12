"""One test per spec rule (R1-R5, H1-H3): a triggering case and its neighbour."""

import re
from datetime import date

from app.domain.enums import Outcome
from app.reference.schemas import PurchaseOrderLine
from app.rules.engine import RuleSource, evaluate
from app.rules.parameters import RuleParameters
from rules_builders import PO, codes, make_context, make_invoice, make_line, make_po, make_supplier

P = RuleParameters()


def test_base_case_is_auto_pay_without_reasons():
    decision = evaluate(make_invoice(), make_context(), P)
    assert decision.outcome is Outcome.AUTO_PAY
    assert decision.reasons == []


# R1: no PO referenced
def test_r1_triggers_when_no_po_is_referenced():
    invoice = make_invoice(po_number=None)
    decision = evaluate(invoice, make_context(line_mapping={1: None}), P)
    assert "R1" in codes(decision)
    assert decision.outcome is Outcome.REJECTED


def test_r1_neighbour_po_found_through_line_mapping():
    invoice = make_invoice(po_number=None)
    decision = evaluate(invoice, make_context(), P)  # line mapping still gives the PO
    assert "R1" not in codes(decision)
    assert decision.outcome is Outcome.AUTO_PAY


# R2: referenced PO does not exist
def test_r2_triggers_when_po_is_missing_from_master():
    invoice = make_invoice(po_number="PO-2026-0199")
    context = make_context(
        purchase_orders={},
        missing_po_numbers=["PO-2026-0199"],
        received_by_po_line={},
        line_mapping={1: None},
    )
    decision = evaluate(invoice, context, P)
    assert "R2" in codes(decision)
    assert "H3" not in codes(decision)  # PO cannot be determined, so no line mapping (DL-32)
    assert decision.outcome is Outcome.REJECTED


def test_r2_neighbour_no_missing_po():
    assert "R2" not in codes(evaluate(make_invoice(), make_context(), P))


# R3: supplier mismatch
def test_r3_triggers_when_invoice_supplier_differs_from_po_supplier():
    context = make_context(supplier=make_supplier(supplier_id="SUP-999"))
    decision = evaluate(make_invoice(), context, P)
    assert "R3" in codes(decision)
    assert decision.outcome is Outcome.REJECTED


def test_r3_triggers_when_supplier_is_unresolved():
    decision = evaluate(make_invoice(), make_context(supplier=None), P)
    r3 = [r for r in decision.reasons if r.code == "R3"]
    assert r3 and r3[0].decision_log_ref == "DL-34"


def test_r3_neighbour_matching_supplier():
    assert "R3" not in codes(evaluate(make_invoice(), make_context(), P))


# R4: duplicate
def test_r4_triggers_on_same_supplier_and_invoice_number():
    context = make_context(prior_invoice_keys={("SUP-001", "INV-0001")})
    decision = evaluate(make_invoice(), context, P)
    assert "R4" in codes(decision)
    assert decision.outcome is Outcome.REJECTED


def test_r4_neighbour_different_invoice_number():
    context = make_context(prior_invoice_keys={("SUP-001", "INV-9999")})
    assert "R4" not in codes(evaluate(make_invoice(), context, P))


# R5: invoice dated before the PO
def test_r5_triggers_when_invoice_is_before_po_date():
    decision = evaluate(make_invoice(invoice_date=date(2026, 1, 5)), make_context(), P)
    r5 = [r for r in decision.reasons if r.code == "R5"]
    assert r5 and r5[0].decision_log_ref == "DL-26"
    assert r5[0].source is RuleSource.SPEC
    assert decision.outcome is Outcome.REJECTED


def test_r5_neighbour_same_day_as_po_is_allowed():
    decision = evaluate(make_invoice(invoice_date=date(2026, 1, 10)), make_context(), P)
    assert "R5" not in codes(decision)


# H1: invoiced above received. PO ordered quantity is 20 so A3 does not fire.
def test_h1_triggers_when_invoiced_exceeds_received():
    context = make_context(purchase_orders={PO: make_po(lines=[make_line(quantity_ordered="20")])})
    decision = evaluate(make_invoice(quantity="12"), context, P)
    assert codes(decision) == ["H1"]
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_h1_neighbour_invoiced_equals_received():
    assert "H1" not in codes(evaluate(make_invoice(quantity="10"), make_context(), P))


# H2: unit price beyond tolerance
def test_h2_triggers_when_price_is_beyond_tolerance():
    decision = evaluate(make_invoice(unit_price="6.00"), make_context(), P)  # about 9% above 5.50
    assert "H2" in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_h2_message_formats_amounts_with_two_decimals():
    # PO price arrives as 9.0 from the reference JSON; the message must show 9.00.
    po = make_po(lines=[make_line(unit_price="9.0")])
    decision = evaluate(make_invoice(unit_price="9.90"), make_context(purchase_orders={PO: po}), P)
    message = next(r.message for r in decision.reasons if r.code == "H2")
    assert "9.90" in message
    assert "9.00" in message
    # No amount with a single decimal ("9.0", "9.9").
    assert not re.search(r"\d\.\d(?!\d)", message.split(" by ")[0])


def test_h2_neighbour_price_equal_to_po():
    assert "H2" not in codes(evaluate(make_invoice(), make_context(), P))


# H3: line not mapped to a PO line
def test_h3_triggers_when_line_has_no_mapping():
    decision = evaluate(make_invoice(), make_context(line_mapping={1: None}), P)
    assert "H3" in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_h3_triggers_when_mapped_po_line_does_not_exist():
    decision = evaluate(make_invoice(), make_context(line_mapping={1: (PO, 9)}), P)
    assert "H3" in codes(decision)


def test_h3_neighbour_line_mapped():
    assert "H3" not in codes(evaluate(make_invoice(), make_context(), P))
