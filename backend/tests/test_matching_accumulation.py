"""DL-21: only quantities approved for payment count toward the cumulative check (A8).

The accumulation belongs to the matching layer (phase 4). These tests process two
invoices in sequence on the same PO line, using the real outcome of the first one to
build the context of the second. Received quantity on the line is 10.
"""

import dataclasses
from decimal import Decimal

from app.domain.enums import Outcome
from app.rules.engine import Decision, evaluate
from app.rules.parameters import RuleParameters
from rules_builders import PO, add_approved_quantities, codes, make_context, make_invoice

P = RuleParameters()
MAPPING = {1: (PO, 1)}


def _process(first, second) -> tuple[Decision, Decision]:
    already_invoiced: dict[tuple[str, int], Decimal] = {}
    first_decision = evaluate(first, make_context(), P)
    add_approved_quantities(already_invoiced, first, MAPPING, first_decision)
    context = make_context(already_invoiced_by_po_line=already_invoiced)
    return first_decision, evaluate(second, context, P)


def test_held_invoice_does_not_reserve_quantity():
    # First invoice is held (price 6.00 vs 5.50), so its 6 units reserve nothing.
    first = make_invoice(quantity="6", unit_price="6.00", invoice_number="INV-0001")
    second = make_invoice(quantity="6", invoice_number="INV-0002")
    first_decision, second_decision = _process(first, second)
    assert first_decision.outcome is Outcome.HELD_FOR_REVIEW
    assert second_decision.outcome is Outcome.AUTO_PAY
    assert second_decision.reasons == []


def test_rejected_invoice_does_not_reserve_quantity():
    first = make_invoice(quantity="6", invoice_number="INV-0001", po_number=None)
    first_decision = evaluate(first, make_context(line_mapping={1: None}), P)
    assert first_decision.outcome is Outcome.REJECTED
    already_invoiced: dict[tuple[str, int], Decimal] = {}
    add_approved_quantities(already_invoiced, first, MAPPING, first_decision)
    assert already_invoiced == {}


def test_auto_pay_invoice_reserves_quantity_and_later_excess_is_held():
    # 6 approved, then 6 more: 12 exceeds the 10 received.
    first = make_invoice(quantity="6", invoice_number="INV-0001")
    second = make_invoice(quantity="6", invoice_number="INV-0002")
    first_decision, second_decision = _process(first, second)
    assert first_decision.outcome is Outcome.AUTO_PAY
    assert codes(second_decision) == ["A8"]
    assert second_decision.outcome is Outcome.HELD_FOR_REVIEW


def test_auto_pay_invoice_reserves_quantity_and_later_invoice_within_remaining_is_paid():
    first = make_invoice(quantity="6", invoice_number="INV-0001")
    second = make_invoice(quantity="4", invoice_number="INV-0002")
    _, second_decision = _process(first, second)
    assert second_decision.outcome is Outcome.AUTO_PAY


def test_hold_is_for_the_whole_invoice_with_no_partial_payment():
    first = make_invoice(quantity="6", invoice_number="INV-0001")
    second = make_invoice(quantity="6", invoice_number="INV-0002")
    _, decision = _process(first, second)
    # One outcome for the whole invoice; the Decision carries no payable or partial quantity.
    assert decision.outcome is Outcome.HELD_FOR_REVIEW
    assert {f.name for f in dataclasses.fields(decision)} == {
        "outcome",
        "reasons",
        "parameters_snapshot",
        "evaluated_at",
    }
    assert {f.name for f in dataclasses.fields(decision.reasons[0])} == {
        "code",
        "message",
        "outcome",
        "source",
        "decision_log_ref",
        "line_number",
    }
