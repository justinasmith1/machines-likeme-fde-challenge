"""Domain models. Reference models live in app.reference.schemas and are re-exported here.

The invoice models are a provisional shape for what the AI extraction will produce.
Optional fields are None when the value was illegible/missing (see DL-18, DL-19):
the system never infers values.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.reference.schemas import (
    GoodsReceivedNote,
    GoodsReceivedNoteLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)

__all__ = [
    "ExtractedInvoice",
    "ExtractedInvoiceLine",
    "GoodsReceivedNote",
    "GoodsReceivedNoteLine",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Supplier",
]


class ExtractedInvoiceLine(BaseModel):
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class ExtractedInvoice(BaseModel):
    supplier_name: str | None = None
    supplier_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    po_number: str | None = None
    # True when the PO number is printed but partially illegible (DL-19, DL-31).
    # Distinguishes "illegible" (A7, hold) from "no PO referenced" (R1, reject).
    po_number_illegible: bool = False
    currency: str | None = None
    lines: list[ExtractedInvoiceLine] = []
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None
