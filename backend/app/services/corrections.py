"""Apply a human correction to an extraction payload (DL-12).

A correction is a partial document; anything it does not mention is kept:

    {
      "document_type": "invoice",
      "header": {"invoice_number": "INV-7", "po_references": ["PO-2026-0001"], "total_amount": "121.00"},
      "lines": [{"line_number": 1, "quantity": "4", "po_line_reference": 1}]
    }

For the fields that carry a reading status (invoice_number, invoice_date, po_references,
quantity, unit_price) a plain value means "a person read this": the status becomes "read".
null means the field is not on the document: the status becomes "absent".

The result is validated by the caller with the same schema as a model answer.
"""

import copy

from app.services.errors import ValidationError

_READ_HEADER_FIELDS = ("invoice_number", "invoice_date")
_PLAIN_HEADER_FIELDS = (
    "supplier_name",
    "supplier_tax_id",
    "due_date",
    "currency",
    "subtotal",
    "tax_amount",
    "total_amount",
)
_READ_LINE_FIELDS = ("quantity", "unit_price")
_PLAIN_LINE_FIELDS = ("description", "line_total", "po_line_reference")


def _read_field(value) -> dict:
    if value is None:
        return {"value": None, "status": "absent", "suggested_value": None}
    return {"value": str(value), "status": "read", "suggested_value": None}


def _plain(name: str, value):
    if value is None or name == "po_line_reference":
        return value
    return str(value)


def apply_corrections(payload: dict, corrections: dict) -> dict:
    if not isinstance(corrections, dict) or not corrections:
        raise ValidationError("No correction was given.")
    unknown = sorted(set(corrections) - {"document_type", "classification_reason", "header", "lines"})
    if unknown:
        raise ValidationError(f"Unknown correction field(s): {', '.join(unknown)}.")

    result = copy.deepcopy(payload)
    for name in ("document_type", "classification_reason"):
        if name in corrections:
            result[name] = corrections[name]

    header = corrections.get("header", {})
    allowed = {*_READ_HEADER_FIELDS, *_PLAIN_HEADER_FIELDS, "po_references"}
    unknown = sorted(set(header) - allowed)
    if unknown:
        raise ValidationError(f"Unknown header field(s): {', '.join(unknown)}.")
    for name, value in header.items():
        if name in _READ_HEADER_FIELDS:
            result["header"][name] = _read_field(value)
        elif name == "po_references":
            values = [str(v) for v in (value or [])]
            result["header"][name] = {
                "values": values,
                "status": "read" if values else "absent",
                "suggested_values": [],
            }
        else:
            result["header"][name] = _plain(name, value)

    for correction in corrections.get("lines", []):
        number = correction.get("line_number")
        if not isinstance(number, int) or not 1 <= number <= len(result["lines"]):
            raise ValidationError(
                f"Line {number!r} does not exist; the invoice has {len(result['lines'])} line(s)."
            )
        unknown = sorted(set(correction) - {"line_number", *_READ_LINE_FIELDS, *_PLAIN_LINE_FIELDS})
        if unknown:
            raise ValidationError(f"Unknown line field(s): {', '.join(unknown)}.")
        line = result["lines"][number - 1]
        for name, value in correction.items():
            if name in _READ_LINE_FIELDS:
                line[name] = _read_field(value)
            elif name in _PLAIN_LINE_FIELDS:
                line[name] = _plain(name, value)
    return result


# ---- what a correction changed, field by field

_READ_FIELDS = {*_READ_HEADER_FIELDS, *_READ_LINE_FIELDS}


def _state(container: dict, name: str) -> dict:
    """A field as {"value", "status"}. Plain fields have no reading status."""
    raw = container.get(name)
    if name == "po_references":
        raw = raw or {}
        return {"value": list(raw.get("values") or []), "status": raw.get("status")}
    if name in _READ_FIELDS:
        raw = raw or {}
        return {"value": raw.get("value"), "status": raw.get("status")}
    return {"value": raw, "status": None}


def diff_extractions(before: dict, after: dict) -> list[dict]:
    """Every field that differs between two extraction payloads.

    Each change is {"scope": "document" | "header" | "line", "line_number", "field",
    "before": {"value", "status"}, "after": {"value", "status"}}. It is stored with the
    audit event of a correction, and used to say who last set each field.
    """
    changes: list[dict] = []

    def compare(scope: str, line_number: int | None, name: str, old: dict, new: dict) -> None:
        previous, current = _state(old, name), _state(new, name)
        if previous != current:
            changes.append(
                {
                    "scope": scope,
                    "line_number": line_number,
                    "field": name,
                    "before": previous,
                    "after": current,
                }
            )

    compare("document", None, "document_type", before, after)
    old_header, new_header = before.get("header") or {}, after.get("header") or {}
    for name in (*_READ_HEADER_FIELDS, "po_references", *_PLAIN_HEADER_FIELDS):
        compare("header", None, name, old_header, new_header)
    old_lines, new_lines = before.get("lines") or [], after.get("lines") or []
    for index in range(min(len(old_lines), len(new_lines))):
        for name in (*_READ_LINE_FIELDS, *_PLAIN_LINE_FIELDS):
            compare("line", index + 1, name, old_lines[index], new_lines[index])
    return changes
