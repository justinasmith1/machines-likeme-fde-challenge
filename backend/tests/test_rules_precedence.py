"""Precedence, tolerance borders, zero prices, GRN summing, determinism and snapshots."""

import dataclasses
from decimal import Decimal

from app.domain.enums import Outcome
from app.domain.models import ExtractedInvoiceLine
from app.rules.engine import evaluate
from app.rules.parameters import RuleParameters
from rules_builders import (
    PO,
    codes,
    make_context,
    make_invoice,
    make_line,
    make_po,
    received_by_po_line,
)
from app.reference.schemas import GoodsReceivedNote, GoodsReceivedNoteLine

P = RuleParameters()


def test_rejected_wins_over_held_and_both_are_reported():
    # Two-line invoice (DL-09): line 1 is on an existing PO with a price 9% above PO (H2);
    # line 2 is on a PO that does not exist (R2). The invoice is rejected and both are reported.
    invoice = make_invoice(unit_price="6.00")
    second = ExtractedInvoiceLine(
        item_code="ITEM-2", description="Gizmo", quantity=Decimal("1"),
        unit_price=Decimal("1.00"), line_total=Decimal("1.00"))
    invoice = invoice.model_copy(update={"lines": [*invoice.lines, second], "subtotal": Decimal("61.00"), "total": Decimal("61.00")})
    context = make_context(
        missing_po_numbers=["PO-2026-0199"],
        received_by_po_line={(PO, 1): Decimal("10")},
        line_mapping={1: (PO, 1), 2: ("PO-2026-0199", 1)},
    )
    decision = evaluate(invoice, context, P)
    assert decision.outcome is Outcome.REJECTED
    assert "R2" in codes(decision)
    assert "H2" in codes(decision)


def test_only_h1_gives_held_for_review():
    context = make_context(purchase_orders={PO: make_po(lines=[make_line(quantity_ordered="20")])})
    decision = evaluate(make_invoice(quantity="12"), context, P)
    assert codes(decision) == ["H1"]
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_no_reason_gives_auto_pay_with_empty_reasons():
    decision = evaluate(make_invoice(), make_context(), P)
    assert decision.outcome is Outcome.AUTO_PAY
    assert decision.reasons == []


def test_evaluation_does_not_short_circuit():
    # Four independent problems: all must be reported, not only the first.
    invoice = make_invoice(quantity="12", unit_price="6.00", invoice_date=None, currency="EUR")
    context = make_context(received_by_po_line={(PO, 1): Decimal("10")})
    decision = evaluate(invoice, context, P)
    assert {"H1", "H2", "A4"} <= set(codes(decision))


# Tolerance borders (DL-02, inclusive). PO price 100.00, invoice quantity 1.
def _price_case(price: str):
    po = make_po(lines=[make_line(quantity_ordered="10", unit_price="100.00")])
    context = make_context(purchase_orders={PO: po})
    return evaluate(make_invoice(quantity="1", unit_price=price), context, P)


def test_tolerance_1_99_percent_does_not_hold():
    assert "H2" not in codes(_price_case("101.99"))


def test_tolerance_2_00_percent_exactly_does_not_hold():
    assert "H2" not in codes(_price_case("102.00"))


def test_tolerance_2_01_percent_holds():
    decision = _price_case("102.01")
    assert "H2" in codes(decision)
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_zero_po_price_with_nonzero_invoice_price_holds_without_dividing():
    po = make_po(lines=[make_line(quantity_ordered="10", unit_price="0")])
    decision = evaluate(
        make_invoice(quantity="1", unit_price="1.00"),
        make_context(purchase_orders={PO: po}),
        P,
    )
    r = next(r for r in decision.reasons if r.code == "H2")
    assert r.decision_log_ref == "DL-30"
    assert decision.outcome is Outcome.HELD_FOR_REVIEW


def test_zero_po_price_with_zero_invoice_price_does_not_hold():
    po = make_po(lines=[make_line(quantity_ordered="10", unit_price="0")])
    decision = evaluate(
        make_invoice(quantity="1", unit_price="0"),
        make_context(purchase_orders={PO: po}),
        P,
    )
    assert "H2" not in codes(decision)


# GRN summing (DL-06 example PO-2026-0102: line 1 received in two GRNs, 10 + 5 = 15)
def _grn(number: str, quantity: str) -> GoodsReceivedNote:
    return GoodsReceivedNote(
        grn_number=number,
        po_number=PO,
        received_date="2026-01-20",
        lines=[GoodsReceivedNoteLine(po_line_number=1, quantity_received=Decimal(quantity))],
    )


def test_two_grns_on_same_po_line_are_summed():
    totals = received_by_po_line([_grn("GRN-1", "10"), _grn("GRN-2", "5")])
    assert totals == {(PO, 1): Decimal("15")}


def test_invoice_covered_by_summed_grns_does_not_hold():
    totals = received_by_po_line([_grn("GRN-1", "10"), _grn("GRN-2", "5")])
    po = make_po(lines=[make_line(quantity_ordered="20")])
    context = make_context(purchase_orders={PO: po}, received_by_po_line=totals)
    assert "H1" not in codes(evaluate(make_invoice(quantity="15"), context, P))


def test_invoice_above_summed_grns_holds():
    totals = received_by_po_line([_grn("GRN-1", "10"), _grn("GRN-2", "5")])
    po = make_po(lines=[make_line(quantity_ordered="20")])
    context = make_context(purchase_orders={PO: po}, received_by_po_line=totals)
    assert "H1" in codes(evaluate(make_invoice(quantity="16"), context, P))


def test_evaluation_is_deterministic():
    invoice = make_invoice(quantity="12", unit_price="6.00", currency="EUR")
    context = make_context()
    first = evaluate(invoice, context, P)
    second = evaluate(invoice, context, P)
    assert first.outcome == second.outcome
    assert sorted(codes(first)) == sorted(codes(second))
    assert first.reasons == second.reasons


def test_decision_snapshots_parameters_and_is_not_affected_by_later_changes():
    params = RuleParameters()
    decision = evaluate(make_invoice(), make_context(), params)
    assert decision.parameters_snapshot == params
    changed = dataclasses.replace(params, price_tolerance_pct=Decimal("0.05"))
    assert decision.parameters_snapshot.price_tolerance_pct == Decimal("0.02")
    assert changed.price_tolerance_pct == Decimal("0.05")


def test_evaluated_at_is_taken_from_context_not_the_clock():
    decision = evaluate(make_invoice(), make_context(), P)
    assert decision.evaluated_at == make_context().evaluated_at
