"""Pydantic models for the three reference JSON files."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PO_NUMBER_PATTERN = r"^PO-\d{4}-\d{4}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"  # ISO 4217 shape


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Supplier(_Strict):
    supplier_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    tax_id: str = Field(min_length=1)
    default_currency: str = Field(pattern=CURRENCY_PATTERN)
    status: Literal["active", "inactive"]
    address: str | None = None


class PurchaseOrderLine(_Strict):
    line_number: int = Field(ge=1)
    item_code: str = Field(min_length=1)
    description: str
    quantity_ordered: Decimal = Field(ge=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrder(_Strict):
    po_number: str = Field(pattern=PO_NUMBER_PATTERN)
    supplier_id: str = Field(min_length=1)
    po_date: date
    currency: str = Field(pattern=CURRENCY_PATTERN)
    status: Literal["open", "partially_invoiced", "closed"]
    lines: list[PurchaseOrderLine]

    @model_validator(mode="after")
    def _unique_line_numbers(self) -> "PurchaseOrder":
        numbers = [line.line_number for line in self.lines]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate line_number within PO")
        return self


class GoodsReceivedNoteLine(_Strict):
    po_line_number: int = Field(ge=1)
    quantity_received: Decimal = Field(ge=0)


class GoodsReceivedNote(_Strict):
    grn_number: str = Field(min_length=1)
    po_number: str = Field(pattern=PO_NUMBER_PATTERN)
    received_date: date
    lines: list[GoodsReceivedNoteLine]
