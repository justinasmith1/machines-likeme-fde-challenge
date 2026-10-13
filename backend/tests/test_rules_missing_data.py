"""No combination of parameters lets an invoice with missing or illegible data reach AUTO_PAY.

Covered data: the purchase order number, the invoice number, the invoice date, and the
quantity and unit price of a line. Each is tried missing or illegible against every
subset of the switchable rules and a spread of tolerances and limits.
"""

import itertools
from decimal import Decimal

import pytest

from app.domain.enums import Outcome
from app.rules.engine import RuleSource, evaluate
from app.rules.parameters import ALL_ASSUMPTION_RULES, RuleParameters
from rules_builders import codes, make_context, make_invoice

RULES = sorted(ALL_ASSUMPTION_RULES)
EVERY_SUBSET = [
    frozenset(subset)
    for size in range(len(RULES) + 1)
    for subset in itertools.combinations(RULES, size)
]
# Generous and strict settings alike: nothing here may open a path to payment.
NUMBERS = [
    {},
    {"price_tolerance_pct": Decimal("0")},
    {"price_tolerance_pct": Decimal("1000"), "rounding_tolerance": Decimal("1000000")},
    {"analyst_approval_limit": Decimal("0")},
    {"analyst_approval_limit": Decimal("1000000000")},
]
EVERY_PARAMETER_SET = [
    RuleParameters(enabled_assumption_rules=rules, **numbers)
    for rules in EVERY_SUBSET
    for numbers in NUMBERS
]

NO_PO_CONTEXT = {"purchase_orders": {}, "received_by_po_line": {}, "line_mapping": {1: None}}

HELD = Outcome.HELD_FOR_REVIEW
CONFIRMED = (RuleSource.CONFIRMED, "DL-18")
ASSUMED_A9 = (RuleSource.ASSUMPTION, "DL-35")

# name -> (invoice, context overrides, the rule that must stop it, the outcome it must give,
#          the origin of that rule: source and decision log reference)
CASES = {
    "purchase order number illegible": (
        make_invoice(po_number=None, po_number_illegible=True), NO_PO_CONTEXT, "A7", HELD, CONFIRMED,
    ),
    "purchase order number absent": (
        make_invoice(po_number=None), NO_PO_CONTEXT, "R1", Outcome.REJECTED, (RuleSource.SPEC, None),
    ),
    "invoice number absent": (make_invoice(invoice_number=None), {}, "A9", HELD, ASSUMED_A9),
    "invoice number empty": (make_invoice(invoice_number=""), {}, "A9", HELD, ASSUMED_A9),
    "invoice number illegible": (
        make_invoice(invoice_number=None, invoice_number_illegible=True), {}, "A9", HELD, CONFIRMED,
    ),
    "invoice date absent": (make_invoice(invoice_date=None), {}, "A9", HELD, ASSUMED_A9),
    "invoice date illegible": (
        make_invoice(invoice_date=None, invoice_date_illegible=True), {}, "A9", HELD, CONFIRMED,
    ),
    "invoice number and date absent": (
        make_invoice(invoice_number=None, invoice_date=None), {}, "A9", HELD, ASSUMED_A9,
    ),
    "invoice number and date illegible": (
        make_invoice(
            invoice_number=None, invoice_date=None, invoice_number_illegible=True, invoice_date_illegible=True
        ),
        {},
        "A9",
        HELD,
        CONFIRMED,
    ),
    "quantity illegible": (make_invoice(quantity=None), {}, "A6", HELD, CONFIRMED),
    "unit price illegible": (make_invoice(unit_price=None, line_total=None), {}, "A6", HELD, CONFIRMED),
    "everything missing but the purchase order": (
        make_invoice(quantity=None, unit_price=None, invoice_number=None, invoice_date=None),
        {},
        "A9",
        HELD,
        ASSUMED_A9,
    ),
    "purchase order illegible and invoice number absent": (
        make_invoice(po_number=None, po_number_illegible=True, invoice_number=None),
        NO_PO_CONTEXT,
        "A7",
        HELD,
        CONFIRMED,
    ),
}


def test_the_parameter_space_is_covered():
    assert RULES == ["A1", "A10", "A2", "A3", "A4", "A5", "A8"]
    assert len(EVERY_SUBSET) == 2 ** len(RULES) == 128
    assert len(EVERY_PARAMETER_SET) == 128 * len(NUMBERS) == 640
    assert frozenset() in EVERY_SUBSET and ALL_ASSUMPTION_RULES in EVERY_SUBSET


@pytest.mark.parametrize("name", CASES)
def test_missing_or_illegible_data_never_reaches_auto_pay(name):
    invoice, overrides, rule, expected, origin = CASES[name]
    context = make_context(**overrides)
    for parameters in EVERY_PARAMETER_SET:
        decision = evaluate(invoice, context, parameters)
        detail = (name, sorted(parameters.enabled_assumption_rules), parameters.price_tolerance_pct)
        assert decision.outcome is not Outcome.AUTO_PAY, detail
        assert decision.outcome is expected, detail
        stopping = [r for r in decision.reasons if r.code == rule]
        assert stopping, detail
        # The origin shown to the reviewer does not depend on the parameters either.
        assert all((r.source, r.decision_log_ref) == origin for r in stopping), detail


def test_the_rules_that_stop_them_cannot_be_switched():
    """A9 for an absent field is tagged as an assumption (DL-35), and is fixed all the same."""
    everything_off = RuleParameters(enabled_assumption_rules=frozenset())
    for name, (invoice, overrides, rule, _, _) in CASES.items():
        assert rule not in ALL_ASSUMPTION_RULES, name
        assert rule in codes(evaluate(invoice, make_context(**overrides), everything_off)), name


def test_the_same_invoice_with_nothing_missing_is_auto_pay_under_every_parameter_set():
    """The control: these parameters do pay a complete, matching invoice."""
    for parameters in EVERY_PARAMETER_SET:
        decision = evaluate(make_invoice(), make_context(), parameters)
        assert decision.outcome is Outcome.AUTO_PAY, sorted(parameters.enabled_assumption_rules)
        assert decision.reasons == []
