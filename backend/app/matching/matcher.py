"""Matching layer: builds the MatchingContext the rules engine needs, from the database.

Everything here is deterministic code. The model never maps anything (DL-38).

Supplier (DL-43, DL-34):
  1. normalized tax identifier equal to exactly one supplier in the master;
  2. otherwise normalized legal name equal to exactly one supplier;
  3. otherwise unresolved, and the engine applies R3.

Purchase orders: every PO number read from the document is looked up. Existing ones go to
the context, the rest are reported as missing (R2).

Line mapping (DL-15), first method that gives exactly one PO line:
  1. PRINTED_PO_LINE: the PO line number printed on the document, if that line exists;
  2. DESCRIPTION: normalized description identical to a PO line description;
  3. ITEM_CODE: a PO line item code contained in the invoice line description;
  4. UNMAPPED: no single match (engine rule H3).

Quantities already invoiced (DL-21, DL-36): only invoices approved for payment count,
that is AUTO_PAY or approved by a person. Held, rejected and undecided invoices reserve
nothing.

Duplicates (DL-25): any invoice uploaded earlier with the same supplier and invoice
number, whatever its status. "Earlier" is upload order, so the result does not depend on
which of two copies happens to be processed first.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.invoice_tables import EvaluationRow, InvoiceRow
from app.db.tables import (
    GoodsReceivedNoteLineRow,
    GoodsReceivedNoteRow,
    PurchaseOrderRow,
    SupplierRow,
)
from app.domain.enums import InvoiceStatus
from app.domain.identifiers import normalize_name, normalize_tax_id
from app.extraction.mapper import MappedInvoice
from app.reference.schemas import PurchaseOrder, PurchaseOrderLine, Supplier
from app.rules.engine import MatchingContext

# Statuses that mean "approved for payment" (DL-21, DL-36).
PAYMENT_APPROVED_STATUSES = frozenset(
    {
        InvoiceStatus.AUTO_PAY.value,
        InvoiceStatus.APPROVED_FOR_PAYMENT.value,
        InvoiceStatus.OVERRIDDEN.value,
    }
)

SUPPLIER_BY_TAX_ID = "TAX_ID"
SUPPLIER_BY_NAME = "NAME"
SUPPLIER_UNRESOLVED = "UNRESOLVED"

PRINTED_PO_LINE = "PRINTED_PO_LINE"
DESCRIPTION = "DESCRIPTION"
ITEM_CODE = "ITEM_CODE"
UNMAPPED = "UNMAPPED"


@dataclass(frozen=True)
class LineMatch:
    line_number: int  # 1-based position on the invoice
    po_number: str | None
    po_line_number: int | None
    method: str
    quantity: Decimal | None


@dataclass(frozen=True)
class MatchResult:
    context: MatchingContext
    supplier: Supplier | None
    supplier_method: str
    po_references: list[str]
    missing_po_numbers: list[str]
    line_matches: list[LineMatch]

    def to_json(self) -> dict:
        """Stored with the evaluation: how the invoice was matched."""
        return {
            "supplier_id": self.supplier.supplier_id if self.supplier else None,
            "supplier_method": self.supplier_method,
            "po_references": self.po_references,
            "missing_po_numbers": self.missing_po_numbers,
            "lines": [
                {
                    "line_number": m.line_number,
                    "po_number": m.po_number,
                    "po_line_number": m.po_line_number,
                    "method": m.method,
                    "quantity": str(m.quantity) if m.quantity is not None else None,
                }
                for m in self.line_matches
            ],
        }


# ---- supplier


def _to_supplier(row: SupplierRow) -> Supplier:
    return Supplier(
        supplier_id=row.supplier_id,
        legal_name=row.legal_name,
        tax_id=row.tax_id,
        default_currency=row.default_currency,
        status=row.status,
        address=row.address,
    )


def resolve_supplier(
    session: Session, tax_id: str | None, name: str | None
) -> tuple[Supplier | None, str]:
    """Supplier from the master and the method that found it. Ambiguity resolves to none."""
    rows = list(session.scalars(select(SupplierRow)))

    wanted_tax_id = normalize_tax_id(tax_id)
    if wanted_tax_id:
        by_tax_id = [r for r in rows if normalize_tax_id(r.tax_id) == wanted_tax_id]
        if len(by_tax_id) == 1:
            return _to_supplier(by_tax_id[0]), SUPPLIER_BY_TAX_ID

    wanted_name = normalize_name(name)
    if wanted_name:
        by_name = [r for r in rows if normalize_name(r.legal_name) == wanted_name]
        if len(by_name) == 1:
            return _to_supplier(by_name[0]), SUPPLIER_BY_NAME

    return None, SUPPLIER_UNRESOLVED


# ---- purchase orders and receipts


def _to_purchase_order(row: PurchaseOrderRow) -> PurchaseOrder:
    return PurchaseOrder(
        po_number=row.po_number,
        supplier_id=row.supplier_id,
        po_date=row.po_date,
        currency=row.currency,
        status=row.status,
        lines=[
            PurchaseOrderLine(
                line_number=line.line_number,
                item_code=line.item_code,
                description=line.description,
                quantity_ordered=line.quantity_ordered,
                unit_price=line.unit_price,
            )
            for line in sorted(row.lines, key=lambda ln: ln.line_number)
        ],
    )


def _received_by_po_line(session: Session, po_numbers: list[str]) -> dict[tuple[str, int], Decimal]:
    """Sum of every GRN line per (po_number, po_line_number)."""
    totals: dict[tuple[str, int], Decimal] = {}
    if not po_numbers:
        return totals
    rows = session.execute(
        select(
            GoodsReceivedNoteRow.po_number,
            GoodsReceivedNoteLineRow.po_line_number,
            GoodsReceivedNoteLineRow.quantity_received,
        )
        .join(GoodsReceivedNoteLineRow, GoodsReceivedNoteLineRow.grn_number == GoodsReceivedNoteRow.grn_number)
        .where(GoodsReceivedNoteRow.po_number.in_(po_numbers))
    )
    for po_number, po_line_number, quantity in rows:
        key = (po_number, po_line_number)
        totals[key] = totals.get(key, Decimal("0")) + quantity
    return totals


def _already_invoiced_by_po_line(
    session: Session, exclude_invoice_id: int | None
) -> dict[tuple[str, int], Decimal]:
    """Quantities on invoices approved for payment, from each one's latest evaluation."""
    statement = select(InvoiceRow.id).where(InvoiceRow.status.in_(PAYMENT_APPROVED_STATUSES))
    if exclude_invoice_id is not None:
        statement = statement.where(InvoiceRow.id != exclude_invoice_id)
    totals: dict[tuple[str, int], Decimal] = {}
    for invoice_id in session.scalars(statement):
        evaluation = session.scalars(
            select(EvaluationRow)
            .where(EvaluationRow.invoice_id == invoice_id)
            .order_by(EvaluationRow.id.desc())
            .limit(1)
        ).first()
        if evaluation is None:
            continue
        for line in evaluation.matching.get("lines", []):
            if line.get("po_number") and line.get("po_line_number") is not None and line.get("quantity"):
                key = (line["po_number"], int(line["po_line_number"]))
                totals[key] = totals.get(key, Decimal("0")) + Decimal(line["quantity"])
    return totals


def _prior_invoice_keys(
    session: Session, supplier: Supplier | None, invoice_number: str | None, invoice_id: int | None
) -> set[tuple[str, str]]:
    if supplier is None or not invoice_number:
        return set()
    statement = select(InvoiceRow.id).where(
        InvoiceRow.supplier_id == supplier.supplier_id,
        InvoiceRow.invoice_number == invoice_number,
    )
    if invoice_id is not None:
        statement = statement.where(InvoiceRow.id < invoice_id)  # uploaded earlier
    if session.scalars(statement.limit(1)).first() is None:
        return set()
    return {(supplier.supplier_id, invoice_number)}


# ---- line mapping


def map_lines(mapped: MappedInvoice, purchase_orders: dict[str, PurchaseOrder]) -> list[LineMatch]:
    candidates = [
        (po.po_number, line) for po in purchase_orders.values() for line in po.lines
    ]
    matches: list[LineMatch] = []
    for index, line in enumerate(mapped.invoice.lines):
        printed = mapped.line_po_references[index] if index < len(mapped.line_po_references) else None
        description = normalize_name(line.description)

        found: tuple[str, int] | None = None
        method = UNMAPPED
        attempts = (
            (PRINTED_PO_LINE, [c for c in candidates if printed is not None and c[1].line_number == printed]),
            (DESCRIPTION, [c for c in candidates if description and normalize_name(c[1].description) == description]),
            (
                ITEM_CODE,
                [
                    c
                    for c in candidates
                    if description and normalize_name(c[1].item_code) and normalize_name(c[1].item_code) in description
                ],
            ),
        )
        for name, hits in attempts:
            if len(hits) == 1:
                found = (hits[0][0], hits[0][1].line_number)
                method = name
                break

        matches.append(
            LineMatch(
                line_number=index + 1,
                po_number=found[0] if found else None,
                po_line_number=found[1] if found else None,
                method=method,
                quantity=line.quantity,
            )
        )
    return matches


# ---- entry point


def build_context(
    mapped: MappedInvoice,
    session: Session,
    *,
    evaluated_at: datetime,
    invoice_id: int | None = None,
) -> MatchResult:
    """Resolve everything the engine needs for one invoice.

    invoice_id is the invoice being evaluated, so that it is not counted against itself.
    """
    invoice = mapped.invoice
    supplier, supplier_method = resolve_supplier(
        session, invoice.supplier_tax_id, invoice.supplier_name
    )

    references = list(dict.fromkeys(mapped.po_references or ([invoice.po_number] if invoice.po_number else [])))
    purchase_orders: dict[str, PurchaseOrder] = {}
    missing: list[str] = []
    for number in references:
        row = session.get(PurchaseOrderRow, number)
        if row is None:
            missing.append(number)
        else:
            purchase_orders[number] = _to_purchase_order(row)

    line_matches = map_lines(mapped, purchase_orders)
    context = MatchingContext(
        supplier=supplier,
        purchase_orders=purchase_orders,
        missing_po_numbers=missing,
        received_by_po_line=_received_by_po_line(session, list(purchase_orders)),
        already_invoiced_by_po_line=_already_invoiced_by_po_line(session, invoice_id),
        prior_invoice_keys=_prior_invoice_keys(session, supplier, invoice.invoice_number, invoice_id),
        line_mapping={
            m.line_number: (m.po_number, m.po_line_number) if m.po_number is not None else None
            for m in line_matches
        },
        evaluated_at=evaluated_at,
    )
    return MatchResult(
        context=context,
        supplier=supplier,
        supplier_method=supplier_method,
        po_references=references,
        missing_po_numbers=missing,
        line_matches=line_matches,
    )
