"""What the model must return: the JSON schema sent to the API and the Pydantic models
that validate the answer.

Critical fields (invoice_number, invoice_date, po_references, quantity, unit_price) carry
a reading status next to the value:
  - "read":      printed and legible; value is set.
  - "illegible": printed but covered, blurred or cut off; value is null.
  - "absent":    not printed on the document; value is null.
A suggested value may accompany an illegible field. It is shown to the analyst as a
suggestion and never reaches the rules engine (DL-18, DL-39).

Amounts are decimal strings, never floats. Dates are ISO (YYYY-MM-DD).
Bump SCHEMA_VERSION on any change: it is part of the cache key (DL-40).
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_VERSION = "1"
SCHEMA_NAME = "invoice_extraction"

DocumentType = Literal["invoice", "quotation", "credit_note", "other"]
ReadStatus = Literal["read", "illegible", "absent"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_string_is_null(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class _ReadField(_Model):
    """A value with its reading status. Enforces: not read means no value."""

    status: ReadStatus

    @model_validator(mode="after")
    def _value_only_when_read(self):
        if self.status == "read":
            if self.value is None:
                raise ValueError("status is 'read' but value is null")
        elif self.value is not None:
            # The model must not put a guess in `value`. Keep it only as a suggestion.
            if self.suggested_value is None:
                self.suggested_value = self.value
            self.value = None
        return self


class ReadText(_ReadField):
    value: str | None
    suggested_value: str | None = None


class ReadDate(_ReadField):
    value: date | None
    suggested_value: date | None = None


class ReadDecimal(_ReadField):
    value: Decimal | None
    suggested_value: Decimal | None = None


class ReadPoReferences(_Model):
    values: list[str]
    status: ReadStatus
    suggested_values: list[str] = []

    @model_validator(mode="after")
    def _values_only_when_read(self):
        if self.status == "read":
            if not self.values:
                raise ValueError("status is 'read' but no purchase order reference is given")
        elif self.values:
            if not self.suggested_values:
                self.suggested_values = self.values
            self.values = []
        return self


class HeaderExtraction(_Model):
    supplier_name: str | None
    supplier_tax_id: str | None
    invoice_number: ReadText
    invoice_date: ReadDate
    due_date: date | None
    po_references: ReadPoReferences
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None


class LineExtraction(_Model):
    description: str | None
    quantity: ReadDecimal
    unit_price: ReadDecimal
    line_total: Decimal | None
    po_line_reference: int | None


class ModelExtraction(_Model):
    document_type: DocumentType
    classification_reason: str
    header: HeaderExtraction
    lines: list[LineExtraction]


# ---- JSON schema for structured outputs (strict mode: every property required,
# ---- no additional properties, nullability expressed in the type).

_NULLABLE_STRING = {"type": ["string", "null"]}
_STATUS = {"type": "string", "enum": ["read", "illegible", "absent"]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _read_field(description: str) -> dict[str, Any]:
    return _object(
        {
            "value": {"type": ["string", "null"], "description": description},
            "status": _STATUS,
            "suggested_value": {
                "type": ["string", "null"],
                "description": "Only when status is illegible and the value can be inferred. Otherwise null.",
            },
        }
    )


EXTRACTION_JSON_SCHEMA: dict[str, Any] = _object(
    {
        "document_type": {
            "type": "string",
            "enum": ["invoice", "quotation", "credit_note", "other"],
        },
        "classification_reason": {
            "type": "string",
            "description": "One short sentence explaining the document type.",
        },
        "header": _object(
            {
                "supplier_name": _NULLABLE_STRING,
                "supplier_tax_id": _NULLABLE_STRING,
                "invoice_number": _read_field("Document number exactly as printed."),
                "invoice_date": _read_field("Issue date as YYYY-MM-DD."),
                "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null."},
                "po_references": _object(
                    {
                        "values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Purchase order numbers exactly as printed. Empty unless status is read.",
                        },
                        "status": _STATUS,
                        "suggested_values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Best guesses when status is illegible. Otherwise empty.",
                        },
                    }
                ),
                "currency": {
                    "type": ["string", "null"],
                    "description": "ISO 4217 code, for example USD.",
                },
                "subtotal": {"type": ["string", "null"], "description": "Decimal string."},
                "tax_amount": {"type": ["string", "null"], "description": "Decimal string."},
                "total_amount": {"type": ["string", "null"], "description": "Decimal string."},
            }
        ),
        "lines": {
            "type": "array",
            "items": _object(
                {
                    "description": _NULLABLE_STRING,
                    "quantity": _read_field("Decimal string."),
                    "unit_price": _read_field("Decimal string."),
                    "line_total": {"type": ["string", "null"], "description": "Decimal string."},
                    "po_line_reference": {
                        "type": ["integer", "null"],
                        "description": "PO line number only if printed on the document. Otherwise null.",
                    },
                }
            ),
        },
    }
)
