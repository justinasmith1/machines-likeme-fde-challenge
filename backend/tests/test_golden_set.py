"""Golden set: expected result per document.

Inputs are real data and are not committed (data/ is git-ignored). See golden_harness.
If the reference data or the extractions are missing the cases are skipped, never failed.
Documents go through the production matching layer and the rules engine, in order.

Each verified extraction uses ExtractedInvoice field names. Each line also carries
po_line_reference, which is not part of the model and is read here for the line mapping.
A fixture may carry document_type; anything other than "invoice" must end as
NOT_AN_INVOICE without reaching the rules engine (DL-16), and its expected_outcome in the
golden set is null.

Only the outcome is compared. expected_reasons is plain-language text for humans.
"""

import json
from dataclasses import dataclass

import pytest

from app.domain.enums import InvoiceStatus
from app.db.invoice_tables import EvaluationRow
from app.rules.engine import Decision
from golden_harness import (
    EXTRACTIONS_DIR,
    GOLDEN,
    REFERENCE_DIR,
    GoldenRunner,
    extraction_from_verified_non_invoice,
    mapped_from_verified,
    verified_extraction_path,
)


@dataclass(frozen=True)
class GoldenResult:
    status: InvoiceStatus
    decision: Decision | None  # None when the engine did not run


@pytest.fixture(scope="module")
def golden_results() -> dict[str, GoldenResult]:
    """Process every document that has a verified extraction, in golden-set order."""
    runner = GoldenRunner.load()
    if runner is None:
        pytest.skip(f"reference data not found in {REFERENCE_DIR} (data/ is git-ignored)")
    if not EXTRACTIONS_DIR.is_dir():
        pytest.skip(f"extractions not found in {EXTRACTIONS_DIR} (data/ is git-ignored)")

    results: dict[str, GoldenResult] = {}
    for case in GOLDEN:
        path = verified_extraction_path(case["filename"])
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))

        document_type = raw.get("document_type", "invoice")
        if document_type != "invoice":
            # Through the invoice service, as an uploaded document: it must be recorded
            # as not an invoice, with no engine evaluation at all.
            invoice = runner.submit_document(
                extraction_from_verified_non_invoice(raw), case["filename"]
            )
            evaluations = runner.session.query(EvaluationRow).filter_by(invoice_id=invoice.id).count()
            assert evaluations == 0, "a non-invoice document reached the rules engine"
            results[case["filename"]] = GoldenResult(InvoiceStatus(invoice.status), None)
            continue

        decision, _ = runner.evaluate(mapped_from_verified(raw), case["filename"], payload=raw)
        results[case["filename"]] = GoldenResult(InvoiceStatus[decision.outcome.name], decision)

    return results


@pytest.mark.parametrize("case", [pytest.param(c, id=c["filename"]) for c in GOLDEN])
def test_golden_outcome(case, golden_results):
    result = golden_results.get(case["filename"])
    if result is None:
        pytest.skip(f"no extraction for {case['filename']} in {EXTRACTIONS_DIR}")

    if case["expected_outcome"] is None:
        # Not an invoice: recorded as such, and the engine never ran.
        assert case["document_type"] != "invoice"
        assert result.status is InvoiceStatus.NOT_AN_INVOICE
        assert result.decision is None
        return

    reasons = [f"{r.code}: {r.message}" for r in result.decision.reasons]
    assert result.decision.outcome.name == case["expected_outcome"], reasons
