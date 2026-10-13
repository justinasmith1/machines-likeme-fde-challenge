"""What a correction changed, field by field (used by the audit trail and field origins)."""

import copy

from app.services.corrections import apply_corrections, diff_extractions
from extraction_builders import make_payload, not_read


def _payload() -> dict:
    payload = make_payload()
    payload["lines"][0]["quantity"] = not_read("illegible", suggested="4")
    return payload


def test_identical_payloads_have_no_changes():
    assert diff_extractions(_payload(), _payload()) == []


def test_a_read_field_reports_value_and_status_before_and_after():
    before = _payload()
    after = apply_corrections(before, {"lines": [{"line_number": 1, "quantity": "4"}]})
    assert diff_extractions(before, after) == [
        {
            "scope": "line",
            "line_number": 1,
            "field": "quantity",
            "before": {"value": None, "status": "illegible"},
            "after": {"value": "4", "status": "read"},
        }
    ]


def test_plain_fields_purchase_orders_and_document_type():
    before = _payload()
    after = apply_corrections(
        before,
        {
            "document_type": "quotation",
            "header": {"currency": "USD", "po_references": [], "invoice_number": None},
            "lines": [{"line_number": 1, "description": "Other", "po_line_reference": 2}],
        },
    )
    changes = {(c["scope"], c["line_number"], c["field"]): (c["before"], c["after"]) for c in diff_extractions(before, after)}
    assert changes[("document", None, "document_type")] == (
        {"value": "invoice", "status": None}, {"value": "quotation", "status": None},
    )
    assert changes[("header", None, "currency")] == ({"value": "EUR", "status": None}, {"value": "USD", "status": None})
    assert changes[("header", None, "po_references")] == (
        {"value": ["PO-2026-0002"], "status": "read"}, {"value": [], "status": "absent"},
    )
    assert changes[("header", None, "invoice_number")] == (
        {"value": "TEST-0001", "status": "read"}, {"value": None, "status": "absent"},
    )
    assert changes[("line", 1, "description")][1] == {"value": "Other", "status": None}
    assert changes[("line", 1, "po_line_reference")] == ({"value": 1, "status": None}, {"value": 2, "status": None})
    assert len(changes) == 6


def test_a_suggestion_alone_is_not_a_change():
    before = _payload()
    after = copy.deepcopy(before)
    after["lines"][0]["quantity"]["suggested_value"] = "5"
    assert diff_extractions(before, after) == []
