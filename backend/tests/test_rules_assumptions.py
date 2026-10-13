"""One test per assumption rule (A1-A8): a triggering case, its neighbour, and the off switch."""

from datetime import date
from decimal import Decimal

from app.domain.enums import Outcome
from app.rules.engine import RuleSource, evaluate
from app.rules.parameters import RuleParameters
from rules_builders import PO, codes, make_context, make_invoice, make_line, make_po, make_supplier

P = RuleParameters()


def _reason(decision, code):
    return next(r for r in decision.reasons if r.code == code)


def test_a1_inactive_supplier_holds():
    context = make_context(supplier=make_supplier(status="inactive"))
    decision = evaluate(make_invoice(), context, P)
    assert codes(decision) == ["A1"]
    assert decision.outcome is Outcome.HELD_FOR_REVIEW
    r = _reason(decision, "A1")
    assert r.outcome is Outcome.HELD_FOR_REVIEW
    assert r.source is RuleSource.ASSUMPTION and r.decision_log_ref == "DL-01"
    assert r.message == (
        "Supplier is marked inactive in the supplier master; a human must confirm "
        "the supplier is approved before payment"
    )


def test_a1_inactive_supplier_with_a_rejection_rule_is_rejected():
    # Inactive supplier (A1, hold) plus invoice dated before the PO (R5, reject).
    context = make_context(supplier=make_supplier(status="inactive"))
    decision = evaluate(make_invoice(invoice_date=date(2026, 1, 5)), context, P)
    assert set(codes(decision)) == {"A1", "R5"}
    assert decision.outcome is Outcome.REJECTED


def test_a1_neighbour_active_supplier():
    assert "A1" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a2_closed_po_rejects():
    context = make_context(purchase_orders={PO: make_po(status="closed")})
    decision = evaluate(make_invoice(), context, P)
    assert "A2" in codes(decision)
    assert decision.outcome is Outcome.REJECTED


def test_a2_neighbour_partially_invoiced_is_evaluated_normally():
    context = make_context(purchase_orders={PO: make_po(status="partially_invoiced")})
    decision = evaluate(make_invoice(), context, P)
    assert "A2" not in codes(decision)
    assert decision.outcome is Outcome.AUTO_PAY


def test_a3_invoiced_above_ordered_holds_even_within_received():
    context = make_context(
        purchase_orders={PO: make_po(lines=[make_line(quantity_ordered="10")])},
        received_by_po_line={(PO, 1): Decimal("12")},
    )
    decision = evaluate(make_invoice(quantity="12"), context, P)
    assert codes(decision) == ["A3"]
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a3_neighbour_invoiced_equals_ordered():
    assert "A3" not in codes(evaluate(make_invoice(quantity="10"), make_context(), P))


def test_a4_currency_mismatch_holds():
    decision = evaluate(make_invoice(currency="EUR"), make_context(), P)
    assert "A4" in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a4_neighbour_same_currency():
    assert "A4" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a5_line_total_inconsistent_holds():
    decision = evaluate(make_invoice(line_total="56.00"), make_context(), P)  # 10 x 5.50 = 55.00
    assert "A5" in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a5_neighbour_line_total_one_cent_off_is_within_rounding():
    # DL-17: a difference of exactly 0.01 is inside the tolerance.
    assert "A5" not in codes(evaluate(make_invoice(line_total="55.01"), make_context(), P))


def test_a5_totals_mismatch_holds():
    decision = evaluate(make_invoice(total="56.00"), make_context(), P)  # subtotal 55.00 + tax 0.00
    assert "A5" in codes(decision)


def test_a6_illegible_price_holds_without_inferring_and_without_h2():
    decision = evaluate(make_invoice(unit_price=None, line_total=None), make_context(), P)
    assert "A6" in codes(decision)
    assert "H2" not in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a6_is_confirmed_by_the_client_and_fires_with_every_assumption_off():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    for invoice in (make_invoice(quantity=None), make_invoice(unit_price=None, line_total=None)):
        decision = evaluate(invoice, make_context(), params)
        assert codes(decision) == ["A6"]
        assert decision.outcome is Outcome.HELD_FOR_REVIEW
        r = _reason(decision, "A6")
        assert r.source is RuleSource.CONFIRMED and r.decision_log_ref == "DL-18"


def test_fixed_rules_are_not_in_the_list_of_switchable_rules():
    from app.rules.parameters import ALL_ASSUMPTION_RULES

    assert not {"A6", "A7", "A9"} & ALL_ASSUMPTION_RULES
    assert sorted(ALL_ASSUMPTION_RULES, key=lambda c: int(c[1:])) == ["A1", "A2", "A3", "A4", "A5", "A8", "A10"]
    assert RuleParameters().enabled_assumption_rules == ALL_ASSUMPTION_RULES


def test_a6_neighbour_all_fields_legible():
    assert "A6" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a7_illegible_po_number_holds_and_is_not_r1():
    invoice = make_invoice(po_number=None, po_number_illegible=True)
    decision = evaluate(invoice, make_context(line_mapping={1: None}), P)
    assert "A7" in codes(decision)
    assert "R1" not in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a7_neighbour_po_number_legible():
    assert "A7" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a8_cumulative_quantity_holds_when_single_invoice_fits():
    context = make_context(already_invoiced_by_po_line={(PO, 1): Decimal("8")})
    decision = evaluate(make_invoice(quantity="5"), context, P)  # 8 + 5 = 13 > 10 received
    assert codes(decision) == ["A8"]
    assert _reason(decision, "A8").decision_log_ref == "DL-21"
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a8_does_not_duplicate_h1_when_line_alone_exceeds_received():
    context = make_context(
        purchase_orders={PO: make_po(lines=[make_line(quantity_ordered="20")])},
        already_invoiced_by_po_line={(PO, 1): Decimal("8")},
    )
    decision = evaluate(make_invoice(quantity="12"), context, P)
    assert codes(decision) == ["H1"]  # DL-33


def test_a8_neighbour_no_prior_invoices():
    assert "A8" not in codes(evaluate(make_invoice(quantity="5"), make_context(), P))


def test_disabled_assumption_rule_does_not_fire():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    context = make_context(supplier=make_supplier(status="inactive"))
    decision = evaluate(make_invoice(), context, params)
    assert decision.reasons == []
    assert decision.outcome is Outcome.AUTO_PAY


def test_spec_rules_stay_active_when_assumptions_are_disabled():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    decision = evaluate(make_invoice(invoice_date=date(2026, 1, 5)), make_context(), params)
    assert codes(decision) == ["R5"]


def test_a9_missing_invoice_number_holds():
    decision = evaluate(make_invoice(invoice_number=None), make_context(), P)
    assert codes(decision) == ["A9"]
    r = _reason(decision, "A9")
    # Absent from the document: an assumption (DL-35), although the rule is fixed.
    assert r.source is RuleSource.ASSUMPTION and r.decision_log_ref == "DL-35"
    assert r.message == "Invoice number is missing or unreadable; duplicate check cannot be performed"
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a9_illegible_invoice_number_holds_as_confirmed_by_the_client():
    decision = evaluate(make_invoice(invoice_number=None, invoice_number_illegible=True), make_context(), P)
    assert codes(decision) == ["A9"]
    r = _reason(decision, "A9")
    assert r.source is RuleSource.CONFIRMED and r.decision_log_ref == "DL-18"
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a9_illegible_and_absent_invoice_date_differ_only_in_origin():
    illegible = evaluate(make_invoice(invoice_date=None, invoice_date_illegible=True), make_context(), P)
    absent = evaluate(make_invoice(invoice_date=None), make_context(), P)
    assert codes(illegible) == codes(absent) == ["A9"]
    assert illegible.outcome is absent.outcome is Outcome.HELD_FOR_REVIEW
    assert _reason(illegible, "A9").message == _reason(absent, "A9").message
    assert (_reason(illegible, "A9").source, _reason(illegible, "A9").decision_log_ref) == (RuleSource.CONFIRMED, "DL-18")
    assert (_reason(absent, "A9").source, _reason(absent, "A9").decision_log_ref) == (RuleSource.ASSUMPTION, "DL-35")


def test_a9_origin_is_decided_per_field():
    invoice = make_invoice(invoice_number=None, invoice_number_illegible=True, invoice_date=None)
    decision = evaluate(invoice, make_context(), P)
    assert [(r.code, r.source, r.decision_log_ref) for r in decision.reasons] == [
        ("A9", RuleSource.CONFIRMED, "DL-18"),  # number: illegible
        ("A9", RuleSource.ASSUMPTION, "DL-35"),  # date: absent
    ]


def test_a9_ignores_the_illegible_mark_when_the_value_is_there():
    invoice = make_invoice(invoice_number_illegible=True, invoice_date_illegible=True)
    assert "A9" not in codes(evaluate(invoice, make_context(), P))


def test_a9_missing_invoice_date_holds():
    decision = evaluate(make_invoice(invoice_date=None), make_context(), P)
    assert codes(decision) == ["A9"]
    assert _reason(decision, "A9").message == (
        "Invoice date is missing or unreadable; date check cannot be performed"
    )
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a9_neighbour_number_and_date_present():
    assert "A9" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a9_is_a_fixed_rule_and_fires_with_every_assumption_off():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    decision = evaluate(make_invoice(invoice_number=None, invoice_date=None), make_context(), params)
    assert codes(decision) == ["A9", "A9"]  # one reason per missing field
    # Tagged as an assumption (DL-35) and still not switchable.
    assert all(r.source is RuleSource.ASSUMPTION for r in decision.reasons)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW

    illegible = make_invoice(
        invoice_number=None, invoice_date=None, invoice_number_illegible=True, invoice_date_illegible=True
    )
    decision = evaluate(illegible, make_context(), params)
    assert codes(decision) == ["A9", "A9"]
    assert all(r.source is RuleSource.CONFIRMED for r in decision.reasons)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a7_is_a_fixed_rule_and_fires_with_every_assumption_off():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    invoice = make_invoice(po_number=None, po_number_illegible=True)
    decision = evaluate(invoice, make_context(line_mapping={1: None}), params)
    assert codes(decision) == ["A7"]
    r = _reason(decision, "A7")
    assert r.source is RuleSource.CONFIRMED and r.decision_log_ref == "DL-18"
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def _two_po_context():
    other = make_po(po_number="PO-2026-0009")
    return make_context(purchase_orders={PO: make_po(), "PO-2026-0009": other})


def test_a10_more_than_one_purchase_order_always_holds():
    decision = evaluate(make_invoice(), _two_po_context(), P)
    assert codes(decision) == ["A10"]
    r = _reason(decision, "A10")
    assert r.source is RuleSource.ASSUMPTION and r.decision_log_ref == "DL-37"
    assert r.message == (
        "Invoice references more than one purchase order; an analyst must confirm the split."
    )
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_a10_counts_a_missing_purchase_order_as_a_reference():
    context = make_context(missing_po_numbers=["PO-2026-0199"])
    decision = evaluate(make_invoice(), context, P)
    assert {"A10", "R2"} <= set(codes(decision))
    assert decision.outcome is Outcome.REJECTED  # a rejection still wins


def test_a10_neighbour_single_purchase_order():
    assert "A10" not in codes(evaluate(make_invoice(), make_context(), P))


def test_a10_can_be_switched_off():
    params = RuleParameters(enabled_assumption_rules=frozenset())
    decision = evaluate(make_invoice(), _two_po_context(), params)
    assert decision.reasons == [] and decision.outcome is Outcome.AUTO_PAY
