"""Deterministic rules engine for three-way matching.

Pure: no database, no network, no global clock, no LLM. Same input, same Decision.

Precedence (spec section 7, DL-28):
  1. REJECTED: any rejection rule (R1-R5 from the spec, A2 assumption) rejects.
  2. HELD_FOR_REVIEW: otherwise, any hold rule (H1-H3 from the spec, A1 and A3-A10 assumptions).
  3. AUTO_PAY: the default when no rule fires.

Every rule is evaluated and every triggered rule is reported. Precedence only decides
the outcome; it never stops the evaluation early.

Line numbers are 1-based positions in the invoice's line list.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.domain.enums import Outcome
from app.domain.models import ExtractedInvoice
from app.reference.schemas import PurchaseOrder, PurchaseOrderLine, Supplier
from app.rules.parameters import RuleParameters


class RuleSource(StrEnum):
    SPEC = "SPEC"
    ASSUMPTION = "ASSUMPTION"


@dataclass(frozen=True)
class RuleReason:
    code: str  # "R5", "H2", "A1"
    message: str  # plain accounting language
    outcome: Outcome  # the outcome this rule proposes
    source: RuleSource
    decision_log_ref: str | None = None  # "DL-01"
    line_number: int | None = None


@dataclass(frozen=True)
class MatchingContext:
    """Everything the engine needs, resolved beforehand by the matching layer (phase 4).

    The engine does not look anything up. Missing or unresolved data is passed as None
    or as an entry in missing_po_numbers.
    """

    supplier: Supplier | None  # resolved from the master; None if not found
    purchase_orders: dict[str, PurchaseOrder]  # referenced POs that exist
    missing_po_numbers: list[str]  # referenced POs that do not exist (R2)
    received_by_po_line: dict[tuple[str, int], Decimal]  # sum of all GRNs per PO line
    already_invoiced_by_po_line: dict[tuple[str, int], Decimal]  # approved for payment earlier (DL-21)
    prior_invoice_keys: set[tuple[str, str]]  # (supplier_id, invoice_number) processed before (R4)
    line_mapping: dict[int, tuple[str, int] | None]  # invoice line -> (po_number, po_line) (H3)
    evaluated_at: datetime  # injected, never read from the clock


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasons: list[RuleReason]  # every triggered rule, not only the winning one
    parameters_snapshot: RuleParameters  # DL-11
    evaluated_at: datetime


def _reason(
    code: str,
    message: str,
    outcome: Outcome,
    *,
    source: RuleSource = RuleSource.SPEC,
    decision_log_ref: str | None = None,
    line_number: int | None = None,
) -> RuleReason:
    return RuleReason(
        code=code,
        message=message,
        outcome=outcome,
        source=source,
        decision_log_ref=decision_log_ref,
        line_number=line_number,
    )


def _money(value: Decimal) -> str:
    """Amount for messages: at least two decimals, thousands separator. Presentation only.

    Extra decimals are kept (0.405 stays 0.405) so a message never hides a real difference.
    """
    value = value.normalize()
    if value.as_tuple().exponent > -2:
        value = value.quantize(Decimal("0.01"))
    return f"{value:,f}"


def _quantity(value: Decimal) -> str:
    """Quantity for messages: no trailing zeros (10, not 10.0). Presentation only."""
    return f"{value.normalize():,f}"


def _percent(value: Decimal) -> str:
    return str((value * 100).quantize(Decimal("0.01")))


def _referenced_po_numbers(invoice: ExtractedInvoice, context: MatchingContext) -> list[str]:
    numbers: list[str] = []
    if invoice.po_number:
        numbers.append(invoice.po_number)
    for mapped in context.line_mapping.values():
        if mapped is not None and mapped[0] not in numbers:
            numbers.append(mapped[0])
    return numbers


def _find_po_line(po: PurchaseOrder, line_number: int) -> PurchaseOrderLine | None:
    return next((ln for ln in po.lines if ln.line_number == line_number), None)


def evaluate(
    invoice: ExtractedInvoice,
    context: MatchingContext,
    parameters: RuleParameters,
) -> Decision:
    reasons: list[RuleReason] = []
    enabled = parameters.enabled_assumption_rules
    tolerance = parameters.price_tolerance_pct
    rounding = parameters.rounding_tolerance

    referenced = _referenced_po_numbers(invoice, context)
    existing_pos = [context.purchase_orders[n] for n in referenced if n in context.purchase_orders]
    # Line-level mapping to PO lines is only attempted when the PO is known (DL-32).
    po_determinable = (
        bool(referenced) and not invoice.po_number_illegible and not context.missing_po_numbers
    )

    # ---- Rejection rules ----
    # R1: no PO referenced. An illegible PO number is not "absent" (DL-31, see A7).
    if not referenced and not invoice.po_number_illegible:
        reasons.append(
            _reason("R1", "Invoice does not reference any purchase order", Outcome.REJECTED)
        )

    # R2: referenced PO does not exist.
    for po_number in context.missing_po_numbers:
        reasons.append(
            _reason(
                "R2",
                f"Referenced purchase order {po_number} does not exist",
                Outcome.REJECTED,
            )
        )

    # R3: supplier mismatch between invoice and PO. Unresolved supplier counts as mismatch (DL-34).
    for po in existing_pos:
        if context.supplier is None:
            reasons.append(
                _reason(
                    "R3",
                    f"Invoice supplier could not be matched to the supplier master (PO {po.po_number})",
                    Outcome.REJECTED,
                    decision_log_ref="DL-34",
                )
            )
        elif po.supplier_id != context.supplier.supplier_id:
            reasons.append(
                _reason(
                    "R3",
                    f"Invoice supplier does not match the supplier on purchase order {po.po_number}",
                    Outcome.REJECTED,
                )
            )

    # R4: duplicate of an invoice already processed (DL-06, DL-25).
    if (
        context.supplier is not None
        and invoice.invoice_number
        and (context.supplier.supplier_id, invoice.invoice_number) in context.prior_invoice_keys
    ):
        reasons.append(
            _reason(
                "R4",
                "Duplicate of an invoice already processed (same supplier and invoice number)",
                Outcome.REJECTED,
                decision_log_ref="DL-25",
            )
        )

    # R5: invoice dated before the PO (DL-26).
    for po in existing_pos:
        if invoice.invoice_date and po.po_date and invoice.invoice_date < po.po_date:
            reasons.append(
                _reason(
                    "R5",
                    f"Invoice date {invoice.invoice_date} is earlier than purchase order date {po.po_date}",
                    Outcome.REJECTED,
                    decision_log_ref="DL-26",
                )
            )

    # A9: invoice number or date missing, so R4 / R5 cannot be evaluated (DL-35).
    if "A9" in enabled:
        for value, message in (
            (
                invoice.invoice_number,
                "Invoice number is missing or unreadable; duplicate check cannot be performed",
            ),
            (
                invoice.invoice_date,
                "Invoice date is missing or unreadable; date check cannot be performed",
            ),
        ):
            if not value:
                reasons.append(
                    _reason(
                        "A9",
                        message,
                        Outcome.HELD_FOR_REVIEW,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-35",
                    )
                )

    # A1: inactive supplier (DL-01). A hold, not a rejection: the status can be stale or
    # reversed, so a human confirms. Rejected only when an R rule also fires.
    if "A1" in enabled and context.supplier is not None and context.supplier.status == "inactive":
        reasons.append(
            _reason(
                "A1",
                "Supplier is marked inactive in the supplier master; a human must confirm "
                "the supplier is approved before payment",
                Outcome.HELD_FOR_REVIEW,
                source=RuleSource.ASSUMPTION,
                decision_log_ref="DL-01",
            )
        )

    # A2: closed PO (DL-05).
    if "A2" in enabled:
        for po in existing_pos:
            if po.status == "closed":
                reasons.append(
                    _reason(
                        "A2",
                        f"Purchase order {po.po_number} is closed",
                        Outcome.REJECTED,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-05",
                    )
                )

    # ---- Hold rules: invoice level ----
    # A4: currency differs from the PO (DL-07).
    if "A4" in enabled and invoice.currency:
        for po in existing_pos:
            if po.currency != invoice.currency:
                reasons.append(
                    _reason(
                        "A4",
                        f"Invoice currency {invoice.currency} differs from the currency of purchase order {po.po_number} ({po.currency})",
                        Outcome.HELD_FOR_REVIEW,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-07",
                    )
                )

    # A5 (totals): subtotal + tax must equal total within rounding (DL-08, DL-17).
    if (
        "A5" in enabled
        and invoice.subtotal is not None
        and invoice.tax_amount is not None
        and invoice.total is not None
    ):
        computed_total = invoice.subtotal + invoice.tax_amount
        if abs(computed_total - invoice.total) > rounding:
            reasons.append(
                _reason(
                    "A5",
                    f"Invoice total {_money(invoice.total)} does not equal subtotal plus tax ({_money(computed_total)})",
                    Outcome.HELD_FOR_REVIEW,
                    source=RuleSource.ASSUMPTION,
                    decision_log_ref="DL-08",
                )
            )

    # A10: more than one purchase order on the invoice (DL-37). Always a hold: an analyst
    # confirms how the invoice splits across the orders.
    all_po_numbers = set(referenced) | set(context.purchase_orders) | set(context.missing_po_numbers)
    if "A10" in enabled and len(all_po_numbers) > 1:
        reasons.append(
            _reason(
                "A10",
                "Invoice references more than one purchase order; an analyst must confirm the split.",
                Outcome.HELD_FOR_REVIEW,
                source=RuleSource.ASSUMPTION,
                decision_log_ref="DL-37",
            )
        )

    # A7: PO number printed but partially illegible (DL-19).
    if "A7" in enabled and invoice.po_number_illegible:
        reasons.append(
            _reason(
                "A7",
                "Purchase order number is partially illegible; the system does not guess it",
                Outcome.HELD_FOR_REVIEW,
                source=RuleSource.ASSUMPTION,
                decision_log_ref="DL-19",
            )
        )

    # ---- Hold rules: line level ----
    for index, line in enumerate(invoice.lines):
        n = index + 1
        mapped = context.line_mapping.get(n)

        # H3: line cannot be mapped to a PO line (DL-15, DL-32).
        if mapped is None:
            if po_determinable:
                reasons.append(
                    _reason(
                        "H3",
                        f"Line {n} cannot be matched to a purchase order line",
                        Outcome.HELD_FOR_REVIEW,
                        line_number=n,
                    )
                )
            continue

        po_number, po_line_number = mapped
        po = context.purchase_orders.get(po_number)
        if po is None:
            continue  # missing PO is already rejected by R2
        po_line = _find_po_line(po, po_line_number)
        if po_line is None:
            reasons.append(
                _reason(
                    "H3",
                    f"Line {n} maps to purchase order line {po_line_number}, which does not exist in {po_number}",
                    Outcome.HELD_FOR_REVIEW,
                    line_number=n,
                )
            )
            continue

        key = (po_number, po_line_number)
        received = context.received_by_po_line.get(key, Decimal("0"))
        already = context.already_invoiced_by_po_line.get(key, Decimal("0"))

        # A6: quantity or price illegible. Nothing is inferred (DL-18).
        illegible = [
            label
            for label, value in (("quantity", line.quantity), ("unit price", line.unit_price))
            if value is None
        ]
        if illegible and "A6" in enabled:
            reasons.append(
                _reason(
                    "A6",
                    f"Line {n}: {' and '.join(illegible)} is illegible; the system does not infer it",
                    Outcome.HELD_FOR_REVIEW,
                    source=RuleSource.ASSUMPTION,
                    decision_log_ref="DL-18",
                    line_number=n,
                )
            )

        if line.quantity is not None:
            quantity = line.quantity
            # H1: invoiced above received (sum of all GRNs for the line).
            if quantity > received:
                reasons.append(
                    _reason(
                        "H1",
                        f"Line {n}: quantity invoiced ({_quantity(quantity)}) exceeds quantity received ({_quantity(received)})",
                        Outcome.HELD_FOR_REVIEW,
                        line_number=n,
                    )
                )
            # A8: cumulative across invoices approved for payment (DL-21). Reported only when H1 did not
            # already cover the line, so the same discrepancy is not reported twice (DL-33).
            elif "A8" in enabled and already + quantity > received:
                reasons.append(
                    _reason(
                        "A8",
                        f"Line {n}: cumulative quantity invoiced ({_quantity(already + quantity)}) exceeds quantity received ({_quantity(received)}) across invoices already approved for payment",
                        Outcome.HELD_FOR_REVIEW,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-21",
                        line_number=n,
                    )
                )
            # A3: invoiced above ordered (DL-04).
            if "A3" in enabled and quantity > po_line.quantity_ordered:
                reasons.append(
                    _reason(
                        "A3",
                        f"Line {n}: quantity invoiced ({_quantity(quantity)}) exceeds quantity ordered ({_quantity(po_line.quantity_ordered)})",
                        Outcome.HELD_FOR_REVIEW,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-04",
                        line_number=n,
                    )
                )

        if line.unit_price is not None:
            price = line.unit_price
            po_price = po_line.unit_price
            if po_price == 0:
                # DL-30: no division by zero. Any non-zero price is a hold.
                if price != 0:
                    reasons.append(
                        _reason(
                            "H2",
                            f"Line {n}: unit price {_money(price)} against a purchase order unit price of zero; tolerance cannot be computed",
                            Outcome.HELD_FOR_REVIEW,
                            source=RuleSource.ASSUMPTION,
                            decision_log_ref="DL-30",
                            line_number=n,
                        )
                    )
            else:
                # H2: tolerance is inclusive (DL-02). Exactly at the limit does not hold.
                difference = abs(price - po_price) / po_price
                if difference > tolerance:
                    reasons.append(
                        _reason(
                            "H2",
                            f"Line {n}: unit price {_money(price)} differs from PO price {_money(po_price)} by {_percent(difference)}%, beyond {_percent(tolerance)}% tolerance",
                            Outcome.HELD_FOR_REVIEW,
                            line_number=n,
                        )
                    )

        # A5 (line): line total must equal quantity times unit price within rounding (DL-08, DL-17).
        if (
            "A5" in enabled
            and line.quantity is not None
            and line.unit_price is not None
            and line.line_total is not None
        ):
            expected = line.quantity * line.unit_price
            if abs(expected - line.line_total) > rounding:
                reasons.append(
                    _reason(
                        "A5",
                        f"Line {n}: line total {_money(line.line_total)} does not equal quantity times unit price ({_money(expected)})",
                        Outcome.HELD_FOR_REVIEW,
                        source=RuleSource.ASSUMPTION,
                        decision_log_ref="DL-08",
                        line_number=n,
                    )
                )

    # ---- Precedence (DL-28) ----
    if any(r.outcome is Outcome.REJECTED for r in reasons):
        outcome = Outcome.REJECTED
    elif any(r.outcome is Outcome.HELD_FOR_REVIEW for r in reasons):
        outcome = Outcome.HELD_FOR_REVIEW
    else:
        outcome = Outcome.AUTO_PAY

    return Decision(
        outcome=outcome,
        reasons=reasons,
        parameters_snapshot=parameters.snapshot(),
        evaluated_at=context.evaluated_at,
    )
