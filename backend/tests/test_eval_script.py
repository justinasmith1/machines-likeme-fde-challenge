"""The manual evaluation script is never run against the API here. These tests cover its
pure comparison logic and its refusal to start without credentials."""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.extraction.mapper import to_engine_input
from app.reference.schemas import PurchaseOrder, PurchaseOrderLine
from extraction_builders import make_extraction, make_payload, not_read
from golden_harness import GoldenState

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_extraction_eval.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("run_extraction_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verified(**overrides) -> dict:
    verified = {
        "supplier_name": "Test Supplier B",
        "supplier_tax_id": "TAX-B",
        "invoice_number": "TEST-0001",
        "invoice_date": "2026-02-15",
        "due_date": "2026-03-15",
        "po_number": "PO-2026-0002",
        "currency": "EUR",
        "subtotal": "100.00",
        "tax_amount": "21.00",
        "total": "121.00",
        "lines": [
            {
                "description": "Synthetic test item",
                "quantity": "4",
                "unit_price": "25.00",
                "line_total": "100.00",
                "po_line_reference": "1",
            }
        ],
    }
    verified.update(overrides)
    return verified


def test_matching_extraction_reports_no_differences(script):
    report = script.compare_with_verified(make_extraction(), _verified())
    assert report["header_ok"] and report["lines_ok"] and report["illegible_ok"]
    assert report["header_diffs"] == [] and report["line_diffs"] == []


def test_amounts_compare_by_value_and_text_ignores_case_and_spacing(script):
    verified = _verified(total="121.0", supplier_name="test  supplier b")
    assert script.compare_with_verified(make_extraction(), verified)["header_ok"]


def test_tax_id_is_compared_normalized_and_the_raw_value_is_reported(script):
    extraction = make_extraction(header={"supplier_tax_id": "Tax ID: tax-b"})
    report = script.compare_with_verified(extraction, _verified(supplier_tax_id="TAX-B"))
    assert report["header_ok"]
    assert report["supplier_tax_id_raw"] == "Tax ID: tax-b"
    assert report["supplier_tax_id_normalized"] == "TAXB"

    different = script.compare_with_verified(extraction, _verified(supplier_tax_id="TAX-C"))
    assert [d["field"] for d in different["header_diffs"]] == ["supplier_tax_id"]


def test_header_and_line_differences_are_listed(script):
    verified = _verified(invoice_number="TEST-0009")
    verified["lines"][0]["unit_price"] = "26.00"
    report = script.compare_with_verified(make_extraction(), verified)
    assert report["header_diffs"] == [
        {"field": "invoice_number", "expected": "TEST-0009", "model": "TEST-0001"}
    ]
    assert report["line_diffs"] == [
        {"line": 1, "field": "unit_price", "expected": "26.00", "model": "25.00"}
    ]


def test_field_verified_as_illegible_must_be_reported_illegible(script):
    verified = _verified()
    verified["lines"][0]["quantity"] = None

    line = make_payload()["lines"][0]
    line["quantity"] = not_read("illegible", suggested="4")
    detected = script.compare_with_verified(make_extraction(lines=[line]), verified)
    assert detected["illegible_ok"] and detected["lines_ok"]
    assert detected["illegible_checks"][0]["suggested"] == "4"

    # The model read a value where the reviewer could not: flagged.
    missed = script.compare_with_verified(make_extraction(), verified)
    assert missed["illegible_ok"] is False
    assert missed["illegible_checks"][0]["model_status"] == "read"


def _state() -> GoldenState:
    po = PurchaseOrder(
        po_number="PO-2026-0002",
        supplier_id="SUP-002",
        po_date="2026-02-01",
        currency="EUR",
        status="open",
        lines=[
            PurchaseOrderLine(line_number=1, item_code="ITEM-8", description="Another item",
                              quantity_ordered=Decimal("1"), unit_price=Decimal("1.00")),
            PurchaseOrderLine(line_number=2, item_code="ITEM-9", description="Synthetic Test Item",
                              quantity_ordered=Decimal("4"), unit_price=Decimal("25.00")),
        ],
    )
    return GoldenState({}, {po.po_number: po}, {})


def test_printed_po_line_reference_is_used_first(script):
    mapping, sources = script.map_lines(_state(), to_engine_input(make_extraction()))
    assert mapping == {1: ("PO-2026-0002", 1)}
    assert sources == ["printed on document"]


def test_line_without_printed_reference_falls_back_to_description(script):
    line = make_payload()["lines"][0]
    line["po_line_reference"] = None
    mapping, sources = script.map_lines(_state(), to_engine_input(make_extraction(lines=[line])))
    assert mapping == {1: ("PO-2026-0002", 2)}
    assert sources == ["description match"]


def test_line_with_no_match_stays_unmapped(script):
    line = make_payload()["lines"][0]
    line.update(po_line_reference=None, description="Something else")
    mapping, sources = script.map_lines(_state(), to_engine_input(make_extraction(lines=[line])))
    assert mapping == {1: None}
    assert sources == ["unmapped"]


def test_script_exits_with_a_clear_message_when_the_key_is_missing(script, monkeypatch, capsys):
    settings = Settings(_env_file=None, azure_openai_endpoint=None, azure_openai_api_key=None)
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    assert script.main([]) == 2
    error = capsys.readouterr().err
    assert "Cannot run the evaluation" in error
    assert "AZURE_OPENAI_API_KEY" in error
