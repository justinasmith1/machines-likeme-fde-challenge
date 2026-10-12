"""Golden set: expected outcome per document, run through the engine.

Inputs are real data and are not committed (data/ is git-ignored):
  1. data/reference/ with the three reference JSON files.
  2. data/test-invoices/extracted/INV-XX.extracted.json, one hand-verified extraction per
     document, using ExtractedInvoice field names. Each line also carries
     po_line_reference, which is not part of the model and is read here to build the
     line mapping.
If either is missing the cases are skipped, never failed.

The MatchingContext is built here by hand (the matching layer is phase 4). Documents are
processed in golden-set order, so earlier invoices feed prior_invoice_keys and
already_invoiced_by_po_line of later ones (INV-02 before its copy INV-08).

Only the outcome is compared. expected_reasons is plain-language text for humans.
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.models import ExtractedInvoice
from app.reference.loader import GRNS_FILE, PURCHASE_ORDERS_FILE, SUPPLIERS_FILE, _read_records
from app.reference.schemas import GoodsReceivedNote, PurchaseOrder, Supplier
from app.rules.engine import Decision, MatchingContext, evaluate
from app.rules.parameters import RuleParameters
from rules_builders import add_approved_quantities

REPO = Path(__file__).resolve().parents[2]
REFERENCE_DIR = REPO / "data" / "reference"
EXTRACTIONS_DIR = Path(
    os.environ.get("GOLDEN_EXTRACTIONS_DIR", REPO / "data" / "test-invoices" / "extracted")
)
GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "expected_outcomes.json").read_text(encoding="utf-8")
)
EVALUATED_AT = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def _load_reference():
    names = (SUPPLIERS_FILE, PURCHASE_ORDERS_FILE, GRNS_FILE)
    if not all((REFERENCE_DIR / n).is_file() for n in names):
        return None
    errors: list[str] = []
    suppliers_raw, _ = _read_records(REFERENCE_DIR / SUPPLIERS_FILE, errors)
    pos_raw, _ = _read_records(REFERENCE_DIR / PURCHASE_ORDERS_FILE, errors)
    grns_raw, _ = _read_records(REFERENCE_DIR / GRNS_FILE, errors)
    assert not errors, errors
    suppliers_by_tax_id = {s.tax_id: s for s in map(Supplier.model_validate, suppliers_raw)}
    pos = {p.po_number: p for p in map(PurchaseOrder.model_validate, pos_raw)}
    received: dict[tuple[str, int], Decimal] = {}
    for grn in map(GoodsReceivedNote.model_validate, grns_raw):
        for line in grn.lines:
            key = (grn.po_number, line.po_line_number)
            received[key] = received.get(key, Decimal("0")) + line.quantity_received
    return suppliers_by_tax_id, pos, received


def _line_mapping(raw: dict, po_number: str | None) -> dict[int, tuple[str, int] | None]:
    mapping: dict[int, tuple[str, int] | None] = {}
    for index, line in enumerate(raw["lines"]):
        reference = line.get("po_line_reference")
        mapped = (po_number, int(reference)) if po_number and reference is not None else None
        mapping[index + 1] = mapped
    return mapping


@pytest.fixture(scope="module")
def golden_decisions() -> dict[str, Decision]:
    """Evaluate every document that has an extraction, in golden-set order."""
    refs = _load_reference()
    if refs is None:
        pytest.skip(f"reference data not found in {REFERENCE_DIR} (data/ is git-ignored)")
    if not EXTRACTIONS_DIR.is_dir():
        pytest.skip(f"extractions not found in {EXTRACTIONS_DIR} (data/ is git-ignored)")
    suppliers_by_tax_id, pos, received = refs

    prior_invoice_keys: set[tuple[str, str]] = set()
    already_invoiced: dict[tuple[str, int], Decimal] = {}
    decisions: dict[str, Decision] = {}

    for case in GOLDEN:
        path = EXTRACTIONS_DIR / f"{Path(case['filename']).stem}.extracted.json"
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        invoice = ExtractedInvoice.model_validate(raw)
        supplier = suppliers_by_tax_id.get(invoice.supplier_tax_id)
        mapping = _line_mapping(raw, invoice.po_number)
        referenced = [invoice.po_number] if invoice.po_number else []

        context = MatchingContext(
            supplier=supplier,
            purchase_orders={n: pos[n] for n in referenced if n in pos},
            missing_po_numbers=[n for n in referenced if n not in pos],
            received_by_po_line=dict(received),
            already_invoiced_by_po_line=dict(already_invoiced),
            prior_invoice_keys=set(prior_invoice_keys),
            line_mapping=mapping,
            evaluated_at=EVALUATED_AT,
        )
        decision = evaluate(invoice, context, RuleParameters())
        decisions[case["filename"]] = decision

        # State for the documents that follow.
        # DL-25: any earlier invoice counts for duplicates, whatever its outcome.
        if supplier is not None and invoice.invoice_number:
            prior_invoice_keys.add((supplier.supplier_id, invoice.invoice_number))
        # DL-21: only invoices the engine actually sent to AUTO_PAY reserve quantity.
        add_approved_quantities(already_invoiced, invoice, mapping, decision)

    return decisions


def _params():
    params = []
    for case in GOLDEN:
        marks = []
        if case["expected_outcome"] is None:
            marks.append(pytest.mark.skip(reason="document classification is phase 3 (DL-16)"))
        params.append(pytest.param(case, id=case["filename"], marks=marks))
    return params


@pytest.mark.parametrize("case", _params())
def test_golden_outcome(case, golden_decisions):
    decision = golden_decisions.get(case["filename"])
    if decision is None:
        pytest.skip(f"no extraction for {case['filename']} in {EXTRACTIONS_DIR}")
    reasons = [f"{r.code}: {r.message}" for r in decision.reasons]
    assert decision.outcome.name == case["expected_outcome"], reasons
