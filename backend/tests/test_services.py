"""Invoice services end to end, with the extraction stubbed. No network, no model."""

from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from app.db.invoice_tables import (
    AuditEventRow,
    AuditTrailIsAppendOnly,
    EvaluationRow,
    ExtractionVersionRow,
    HumanDecisionRow,
    RuleParametersRow,
)
from app.domain.enums import InvoiceStatus
from app.services.errors import InvalidState, NotFound, PermissionDenied, ValidationError
from db_builders import (
    ANALYST,
    INTAKE,
    MANAGER,
    Harness,
    extraction_result,
    failed_result,
    seed_reference,
    widget_payload,
)
from extraction_builders import make_payload, not_read, read


@pytest.fixture
def harness(session, tmp_path):
    seed_reference(session)
    return Harness(session, tmp_path)


def _line(**overrides) -> dict:
    line = make_payload()["lines"][0]
    line.update(overrides)
    return line


def _held_payload() -> dict:
    """Clean invoice on PO-2026-0002 except the unit price: 30.00 against 25.00 (H2)."""
    return {
        "header": {"subtotal": "120.00", "tax_amount": "21.00", "total_amount": "141.00"},
        "lines": [_line(unit_price=read("30.00"), line_total="120.00")],
    }


def _large_held_payload() -> dict:
    """Held for price, with a total of 6,000.00: above the 5,000 analyst limit."""
    return {
        "header": {"subtotal": "6000.00", "tax_amount": "0.00", "total_amount": "6000.00"},
        "lines": [_line(unit_price=read("1500.00"), line_total="6000.00")],
    }


def _rejected_payload(total="121.00") -> dict:
    """No purchase order on the document: rejected under R1."""
    return {
        "header": {
            "po_references": {"values": [], "status": "absent", "suggested_values": []},
            "total_amount": total,
            "subtotal": str(Decimal(total) - Decimal("21.00")),
        }
    }


def _codes(harness, invoice_id) -> list[str]:
    return [r["code"] for r in harness.service.latest_evaluation(invoice_id).reasons]


def _count(session, table, invoice_id) -> int:
    return len(list(session.scalars(select(table).where(table.invoice_id == invoice_id))))


# ---- submission and processing


def test_submit_stores_the_file_and_registers_a_pending_invoice(harness):
    harness.extractor.queue.append(extraction_result())
    invoice = harness.service.submit_invoice(b"%PDF-fake", "My Invoice.PDF", INTAKE)

    assert invoice.status == InvoiceStatus.PENDING_EXTRACTION.value
    assert invoice.uploaded_by == "ines.intake" and invoice.filename == "My Invoice.PDF"
    assert invoice.stored_path == f"{invoice.file_sha256}.pdf"
    assert (harness.uploads / invoice.stored_path).read_bytes() == b"%PDF-fake"
    assert harness.events(invoice.id) == ["INVOICE_SUBMITTED"]
    assert harness.extractor.calls == []  # extraction has not run yet


def test_processing_extracts_evaluates_and_stores_everything(harness):
    invoice = harness.submit()

    assert invoice.status == InvoiceStatus.AUTO_PAY.value
    assert (invoice.supplier_id, invoice.invoice_number) == ("SUP-002", "TEST-0001")
    assert (invoice.total, invoice.currency) == (Decimal("121.00"), "EUR")

    version = harness.service.latest_extraction(invoice.id)
    assert (version.version, version.source, version.created_by) == (1, "MODEL", "system")
    assert version.payload["header"]["invoice_number"]["value"] == "TEST-0001"
    assert version.raw_response == {"id": "resp_test"}
    assert version.model == "test-deployment" and version.prompt_version

    evaluation = harness.service.latest_evaluation(invoice.id)
    assert evaluation.outcome == "AUTO_PAY" and evaluation.reasons == []
    assert evaluation.extraction_version_id == version.id
    assert evaluation.parameters_snapshot["version"] == 1
    assert evaluation.parameters_snapshot["price_tolerance_pct"] in ("0.02", "0.020000")
    assert evaluation.reference_data_version == {"suppliers.json": "a" * 64}
    assert evaluation.matching["supplier_method"] == "TAX_ID"
    assert evaluation.matching["lines"][0]["method"] == "PRINTED_PO_LINE"

    assert harness.events(invoice.id) == [
        "INVOICE_SUBMITTED",
        "EXTRACTION_SUCCEEDED",
        "INVOICE_EVALUATED",
    ]


def test_reasons_are_stored_with_code_message_source_and_decision_log(harness):
    invoice = harness.submit(_held_payload())
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    reason = harness.service.latest_evaluation(invoice.id).reasons[0]
    assert reason["code"] == "H2" and reason["source"] == "SPEC"
    assert reason["outcome"] == "HELD_FOR_REVIEW" and reason["line_number"] == 1
    assert "30.00" in reason["message"] and "25.00" in reason["message"]


def test_quotation_ends_as_not_an_invoice_without_an_evaluation(harness):
    invoice = harness.submit({"document_type": "quotation", "classification_reason": "Titled Quotation."})
    assert invoice.status == InvoiceStatus.NOT_AN_INVOICE.value
    assert invoice.status_reason == "Received, not an invoice: quotation (Titled Quotation.)"
    assert _count(harness.session, EvaluationRow, invoice.id) == 0
    assert harness.events(invoice.id)[-1] == "CLASSIFIED_NOT_AN_INVOICE"


@pytest.mark.parametrize("document_type", ["quotation", "credit_note", "other"])
def test_no_non_invoice_document_reaches_the_engine(harness, document_type):
    invoice = harness.submit({"document_type": document_type})
    assert invoice.status == InvoiceStatus.NOT_AN_INVOICE.value
    assert harness.service.latest_evaluation(invoice.id) is None
    assert "INVOICE_EVALUATED" not in harness.events(invoice.id)


def test_only_a_pending_invoice_can_be_processed(harness):
    invoice = harness.submit()
    with pytest.raises(InvalidState):
        harness.service.process_invoice(invoice.id)


def test_unknown_invoice_is_not_found(harness):
    with pytest.raises(NotFound):
        harness.service.process_invoice(999)
    with pytest.raises(NotFound):
        harness.service.decide(999, "APPROVE", "x", ANALYST)


# ---- extraction failure and retry


def test_failed_extraction_can_be_retried(harness):
    invoice = harness.submit(result=failed_result())
    assert invoice.status == InvoiceStatus.EXTRACTION_FAILED.value
    assert invoice.status_reason == "The model service returned HTTP 503."
    assert harness.service.latest_extraction(invoice.id) is None

    harness.service.retry_extraction(invoice.id, INTAKE)
    assert invoice.status == InvoiceStatus.PENDING_EXTRACTION.value
    harness.extractor.queue.append(extraction_result())
    harness.service.process_invoice(invoice.id)

    assert invoice.status == InvoiceStatus.AUTO_PAY.value
    assert harness.events(invoice.id) == [
        "INVOICE_SUBMITTED",
        "EXTRACTION_FAILED",
        "EXTRACTION_RETRY_REQUESTED",
        "EXTRACTION_SUCCEEDED",
        "INVOICE_EVALUATED",
    ]


def test_unexpected_error_while_processing_becomes_a_retryable_failure(harness, caplog):
    secret = "stack detail with internals"

    def broken(file_bytes, filename):
        raise RuntimeError(secret)

    working = harness.service._extract
    harness.service._extract = broken
    invoice = harness.service.submit_invoice(b"content", "invoice.pdf", INTAKE)
    harness.service.process_invoice(invoice.id)  # does not raise

    assert invoice.status == InvoiceStatus.EXTRACTION_FAILED.value
    assert invoice.status_reason == (
        "The document could not be processed because of an unexpected error. It can be retried."
    )
    failure = harness.service.audit_trail(invoice.id)[-1]
    assert failure.event_type == "EXTRACTION_FAILED"
    assert failure.details["error_code"] == "PROCESSING_ERROR"
    assert secret not in str(failure.details) and secret not in invoice.status_reason
    assert secret in caplog.text  # only the server log has the detail

    harness.service._extract = working
    harness.service.retry_extraction(invoice.id, ANALYST)
    harness.extractor.queue.append(extraction_result())
    harness.service.process_invoice(invoice.id)
    assert invoice.status == InvoiceStatus.AUTO_PAY.value
    assert harness.events(invoice.id) == [
        "INVOICE_SUBMITTED",
        "EXTRACTION_FAILED",
        "EXTRACTION_RETRY_REQUESTED",
        "EXTRACTION_SUCCEEDED",
        "INVOICE_EVALUATED",
    ]


def test_error_after_a_successful_extraction_leaves_nothing_half_written(harness, monkeypatch):
    from app.services import invoices as invoices_module

    def broken(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(invoices_module, "evaluate_and_store", broken)
    invoice = harness.submit()

    assert invoice.status == InvoiceStatus.EXTRACTION_FAILED.value
    # The extraction version written before the error was rolled back with it.
    assert harness.service.latest_extraction(invoice.id) is None
    assert harness.service.latest_evaluation(invoice.id) is None
    assert harness.events(invoice.id) == ["INVOICE_SUBMITTED", "EXTRACTION_FAILED"]


def test_retry_only_applies_to_a_failed_extraction(harness):
    invoice = harness.submit()
    with pytest.raises(InvalidState, match="only a failed extraction can be retried"):
        harness.service.retry_extraction(invoice.id, INTAKE)


# ---- correction (DL-12)


def test_correction_adds_a_version_and_re_evaluates_keeping_history(harness):
    illegible = {"lines": [_line(quantity=not_read("illegible", suggested="4"))]}
    invoice = harness.submit(illegible)
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value and _codes(harness, invoice.id) == ["A6"]
    first_evaluation = harness.service.latest_evaluation(invoice.id)

    harness.service.correct_extraction(
        invoice.id, {"lines": [{"line_number": 1, "quantity": "4"}]}, "Read from the paper copy.", ANALYST
    )

    # Everything is in order now, and the evaluation says so, but correcting data never
    # approves payment: the invoice waits for a person (DL-46).
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert harness.service.latest_evaluation(invoice.id).outcome == "AUTO_PAY"
    assert harness.service.latest_evaluation(invoice.id).reasons == []
    assert invoice.status_reason == (
        "Extracted data was corrected by ana.analyst; a human decision is required before payment."
    )
    versions = list(
        harness.session.scalars(
            select(ExtractionVersionRow)
            .where(ExtractionVersionRow.invoice_id == invoice.id)
            .order_by(ExtractionVersionRow.version)
        )
    )
    assert [(v.version, v.source, v.created_by) for v in versions] == [
        (1, "MODEL", "system"),
        (2, "HUMAN_CORRECTION", "ana.analyst"),
    ]
    assert versions[1].comment == "Read from the paper copy."
    assert versions[1].payload["lines"][0]["quantity"] == {
        "value": "4", "status": "read", "suggested_value": None,
    }
    # The model's version and the first evaluation are untouched.
    assert versions[0].payload["lines"][0]["quantity"]["status"] == "illegible"
    assert _count(harness.session, EvaluationRow, invoice.id) == 2
    assert first_evaluation.outcome == "HELD_FOR_REVIEW"
    assert harness.service.latest_evaluation(invoice.id).extraction_version_id == versions[1].id
    assert harness.events(invoice.id)[-3:] == [
        "EXTRACTION_CORRECTED",
        "INVOICE_EVALUATED",
        "HELD_AFTER_CORRECTION",
    ]


def test_correction_can_reclassify_a_document(harness):
    invoice = harness.submit({"document_type": "quotation"})
    harness.service.correct_extraction(
        invoice.id, {"document_type": "invoice"}, "It is an invoice; the title misled.", ANALYST
    )
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert harness.service.latest_evaluation(invoice.id).outcome == "AUTO_PAY"


def test_correcting_an_invoice_into_a_non_invoice_makes_it_not_an_invoice(harness):
    invoice = harness.submit()
    harness.service.correct_extraction(
        invoice.id, {"document_type": "quotation"}, "It is a quotation.", ANALYST
    )
    assert invoice.status == InvoiceStatus.NOT_AN_INVOICE.value


def test_same_analyst_corrects_and_then_approves_as_two_separate_actions(harness):
    illegible = {"lines": [_line(quantity=not_read("illegible"))]}
    invoice = harness.submit(illegible)
    assert harness.events(invoice.id)[0] == "INVOICE_SUBMITTED"
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value

    harness.service.correct_extraction(
        invoice.id, {"lines": [{"line_number": 1, "quantity": "4"}]}, "Read from the paper copy.", ANALYST
    )
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value

    harness.service.decide(invoice.id, "APPROVE", "Checked against the delivery note.", ANALYST)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value
    assert invoice.status_reason is None

    events = [e for e in harness.service.audit_trail(invoice.id) if e.actor == "ana.analyst"]
    assert [e.event_type for e in events] == [
        "EXTRACTION_CORRECTED",
        "HELD_AFTER_CORRECTION",
        "HUMAN_DECISION_RECORDED",
    ]
    assert events[0].details["comment"] == "Read from the paper copy."
    assert events[2].details["comment"] == "Checked against the delivery note."


def test_correction_that_the_engine_rejects_is_held_and_needs_an_override(harness):
    invoice = harness.submit()
    harness.service.correct_extraction(
        invoice.id, {"header": {"po_references": []}}, "No PO on the document.", ANALYST
    )
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert harness.service.latest_evaluation(invoice.id).outcome == "REJECTED"
    with pytest.raises(InvalidState, match="Use OVERRIDE"):
        harness.service.decide(invoice.id, "APPROVE", "Pay.", MANAGER)
    harness.service.decide(invoice.id, "REJECT", "No purchase order.", ANALYST)
    assert invoice.status == InvoiceStatus.REJECTED_BY_REVIEWER.value


def test_correction_after_a_human_decision_supersedes_it_and_re_evaluates(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "APPROVE", "Price agreed.", ANALYST)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value

    harness.service.correct_extraction(
        invoice.id, {"header": {"currency": "USD"}}, "Currency was misread.", ANALYST
    )

    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value  # back in review
    assert set(_codes(harness, invoice.id)) == {"H2", "A4"}
    events = harness.service.audit_trail(invoice.id)
    superseded = [e for e in events if e.event_type == "HUMAN_DECISION_SUPERSEDED"]
    assert len(superseded) == 1
    assert superseded[0].details["previous_status"] == "APPROVED_FOR_PAYMENT"
    assert superseded[0].details["reason"] == "EXTRACTION_CORRECTED"
    # The decision itself stays in the history, marked as superseded.
    decisions = harness.service.decisions(invoice.id)
    assert len(decisions) == 1
    assert decisions[0].superseded_at is not None
    assert decisions[0].superseded_reason == "EXTRACTION_CORRECTED"


def test_correction_after_a_decision_returns_to_review_even_if_all_rules_pass(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "APPROVE", "Price agreed.", ANALYST)
    harness.service.correct_extraction(
        invoice.id,
        {"lines": [{"line_number": 1, "unit_price": "25.00", "line_total": "100.00"}],
         "header": {"subtotal": "100.00", "total_amount": "121.00"}},
        "Price was misread.",
        ANALYST,
    )
    assert harness.service.latest_evaluation(invoice.id).outcome == "AUTO_PAY"
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert harness.service.decisions(invoice.id)[0].superseded_at is not None


def test_correction_needs_a_comment_a_reviewer_and_valid_data(harness):
    invoice = harness.submit(_held_payload())
    fix = {"lines": [{"line_number": 1, "unit_price": "25.00"}]}
    with pytest.raises(ValidationError, match="comment"):
        harness.service.correct_extraction(invoice.id, fix, "   ", ANALYST)
    with pytest.raises(PermissionDenied):
        harness.service.correct_extraction(invoice.id, fix, "Fixing.", INTAKE)
    with pytest.raises(ValidationError, match="not valid"):
        harness.service.correct_extraction(
            invoice.id, {"header": {"total_amount": "abc"}}, "Fixing.", ANALYST
        )
    with pytest.raises(ValidationError, match="Line 7 does not exist"):
        harness.service.correct_extraction(
            invoice.id, {"lines": [{"line_number": 7, "quantity": "1"}]}, "Fixing.", ANALYST
        )
    with pytest.raises(ValidationError, match="Unknown header field"):
        harness.service.correct_extraction(invoice.id, {"header": {"colour": "red"}}, "Fixing.", ANALYST)
    assert _count(harness.session, ExtractionVersionRow, invoice.id) == 1  # nothing was added


def test_failed_extraction_has_nothing_to_correct(harness):
    invoice = harness.submit(result=failed_result())
    with pytest.raises(InvalidState, match="no extraction to correct"):
        harness.service.correct_extraction(invoice.id, {"header": {"currency": "USD"}}, "x", ANALYST)


# ---- human decisions (DL-13)


def test_analyst_approves_a_held_invoice_with_a_comment(harness):
    invoice = harness.submit(_held_payload())
    evaluation = harness.service.latest_evaluation(invoice.id)

    harness.service.decide(invoice.id, "APPROVE", "  Price agreed with purchasing.  ", ANALYST)

    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value
    decision = harness.session.scalars(select(HumanDecisionRow)).one()
    assert decision.decision == "APPROVE"
    assert decision.previous_outcome == "HELD_FOR_REVIEW"
    assert decision.comment == "Price agreed with purchasing."
    assert (decision.decided_by, decision.role, decision.required_role) == (
        "ana.analyst", "AP_ANALYST", "AP_ANALYST",
    )
    assert decision.evaluation_id == evaluation.id
    # The engine's evaluation is not modified by the human decision.
    assert evaluation.outcome == "HELD_FOR_REVIEW"
    assert harness.events(invoice.id)[-1] == "HUMAN_DECISION_RECORDED"


@pytest.mark.parametrize(
    ("decision", "status"),
    [("REJECT", "REJECTED_BY_REVIEWER"), ("REQUEST_CREDIT_NOTE", "CREDIT_NOTE_REQUESTED")],
)
def test_other_decisions_on_a_held_invoice(harness, decision, status):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, decision, "Supplier overcharged.", ANALYST)
    assert invoice.status == status


@pytest.mark.parametrize("comment", ["", "   ", None])
def test_decision_without_a_comment_is_refused(harness, comment):
    invoice = harness.submit(_held_payload())
    with pytest.raises(ValidationError, match="comment is required"):
        harness.service.decide(invoice.id, "APPROVE", comment, ANALYST)
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert _count(harness.session, HumanDecisionRow, invoice.id) == 0


def test_intake_cannot_decide_and_the_attempt_is_audited(harness):
    invoice = harness.submit(_held_payload())
    with pytest.raises(PermissionDenied):
        harness.service.decide(invoice.id, "APPROVE", "Looks fine.", INTAKE)
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    denied = harness.service.audit_trail(invoice.id)[-1]
    assert denied.event_type == "DECISION_DENIED"
    assert (denied.actor, denied.role) == ("ines.intake", "INTAKE")


def test_unknown_decision_is_refused(harness):
    invoice = harness.submit(_held_payload())
    with pytest.raises(ValidationError, match="Unknown decision"):
        harness.service.decide(invoice.id, "PAY_NOW", "x", ANALYST)


def test_analyst_cannot_approve_above_the_limit_and_the_attempt_is_audited(harness):
    invoice = harness.submit(_large_held_payload())
    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value

    with pytest.raises(PermissionDenied, match=r"6,000\.00.*5,000\.00.*AP Manager"):
        harness.service.decide(invoice.id, "APPROVE", "Approved.", ANALYST)

    assert invoice.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert _count(harness.session, HumanDecisionRow, invoice.id) == 0
    denied = harness.service.audit_trail(invoice.id)[-1]
    assert denied.event_type == "DECISION_DENIED"
    assert denied.actor == "ana.analyst"
    assert denied.details["reason"] == "above the analyst approval limit"


def test_manager_approves_above_the_limit(harness):
    invoice = harness.submit(_large_held_payload())
    harness.service.decide(invoice.id, "APPROVE", "Approved after review.", MANAGER)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value
    decision = harness.session.scalars(select(HumanDecisionRow)).one()
    assert (decision.role, decision.required_role) == ("AP_MANAGER", "AP_MANAGER")


def test_analyst_can_reject_above_the_limit(harness):
    # The limit protects the release of payment. Refusing to pay does not need the manager.
    invoice = harness.submit(_large_held_payload())
    harness.service.decide(invoice.id, "REJECT", "Price not agreed.", ANALYST)
    assert invoice.status == InvoiceStatus.REJECTED_BY_REVIEWER.value


def test_total_exactly_at_the_limit_is_within_the_analyst_authority(harness):
    payload = {
        "header": {"subtotal": "5000.00", "tax_amount": "0.00", "total_amount": "5000.00"},
        "lines": [_line(unit_price=read("1250.00"), line_total="5000.00")],
    }
    invoice = harness.submit(payload)
    harness.service.decide(invoice.id, "APPROVE", "Approved.", ANALYST)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value


def test_unknown_total_needs_the_manager_to_release_payment(harness):
    payload = _held_payload()
    payload["header"]["total_amount"] = None
    invoice = harness.submit(payload)
    with pytest.raises(PermissionDenied, match="unknown"):
        harness.service.decide(invoice.id, "APPROVE", "Approved.", ANALYST)
    harness.service.decide(invoice.id, "APPROVE", "Total confirmed by phone.", MANAGER)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value


def test_rejected_invoice_can_be_overridden_with_a_comment(harness):
    invoice = harness.submit(_rejected_payload())
    assert invoice.status == InvoiceStatus.REJECTED.value and "R1" in _codes(harness, invoice.id)

    harness.service.decide(invoice.id, "OVERRIDE", "PO confirmed by purchasing by email.", ANALYST)

    assert invoice.status == InvoiceStatus.OVERRIDDEN.value
    decision = harness.session.scalars(select(HumanDecisionRow)).one()
    assert (decision.decision, decision.previous_outcome) == ("OVERRIDE", "REJECTED")
    assert harness.service.latest_evaluation(invoice.id).outcome == "REJECTED"  # original kept


def test_override_above_the_limit_needs_the_manager(harness):
    invoice = harness.submit(_rejected_payload(total="9000.00"))
    with pytest.raises(PermissionDenied):
        harness.service.decide(invoice.id, "OVERRIDE", "Overriding.", ANALYST)
    harness.service.decide(invoice.id, "OVERRIDE", "Confirmed with purchasing.", MANAGER)
    assert invoice.status == InvoiceStatus.OVERRIDDEN.value


def test_an_auto_pay_invoice_can_be_rejected_by_a_reviewer(harness):
    invoice = harness.submit()
    assert invoice.status == InvoiceStatus.AUTO_PAY.value

    harness.service.decide(invoice.id, "REJECT", "Supplier asked us to cancel it.", ANALYST)

    assert invoice.status == InvoiceStatus.REJECTED_BY_REVIEWER.value
    decision = harness.service.decisions(invoice.id)[0]
    assert (decision.decision, decision.previous_outcome) == ("REJECT", "AUTO_PAY")
    assert harness.service.latest_evaluation(invoice.id).outcome == "AUTO_PAY"  # engine result kept


def test_an_auto_pay_invoice_can_be_sent_back_for_a_credit_note(harness):
    invoice = harness.submit()
    harness.service.decide(invoice.id, "REQUEST_CREDIT_NOTE", "Goods returned.", ANALYST)
    assert invoice.status == InvoiceStatus.CREDIT_NOTE_REQUESTED.value


def test_a_rejected_auto_pay_invoice_no_longer_reserves_quantity(harness):
    first = harness.submit(widget_payload(quantity="6", invoice_number="A-1"))
    harness.service.decide(first.id, "REJECT", "Cancelled by the supplier.", ANALYST)
    second = harness.submit(widget_payload(quantity="6", invoice_number="A-2"))
    assert second.status == InvoiceStatus.AUTO_PAY.value


def test_a_new_decision_supersedes_the_previous_one_and_both_stay_in_history(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "REJECT", "Price not agreed.", ANALYST)
    harness.service.decide(invoice.id, "APPROVE", "Purchasing confirmed the new price.", MANAGER)

    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value
    first, second = harness.service.decisions(invoice.id)
    assert (first.decision, first.decided_by, first.previous_outcome) == (
        "REJECT", "ana.analyst", "HELD_FOR_REVIEW",
    )
    assert first.superseded_at is not None and first.superseded_reason == "NEW_DECISION"
    assert first.comment == "Price not agreed."  # untouched
    assert (second.decision, second.decided_by, second.previous_outcome) == (
        "APPROVE", "max.manager", "REJECTED_BY_REVIEWER",
    )
    assert second.superseded_at is None
    assert harness.events(invoice.id)[-3:] == [
        "HUMAN_DECISION_RECORDED",
        "HUMAN_DECISION_SUPERSEDED",
        "HUMAN_DECISION_RECORDED",
    ]
    last = harness.service.audit_trail(invoice.id)[-1]
    assert last.details["supersedes_decision_ids"] == [first.id]


def test_analyst_cannot_approve_above_the_limit_after_a_previous_decision(harness):
    invoice = harness.submit(_large_held_payload())
    harness.service.decide(invoice.id, "REJECT", "Price not agreed.", ANALYST)

    with pytest.raises(PermissionDenied, match="AP Manager"):
        harness.service.decide(invoice.id, "APPROVE", "Changed my mind.", ANALYST)

    # Nothing changed: the rejection is still the decision in force.
    assert invoice.status == InvoiceStatus.REJECTED_BY_REVIEWER.value
    decisions = harness.service.decisions(invoice.id)
    assert len(decisions) == 1 and decisions[0].superseded_at is None
    assert harness.service.audit_trail(invoice.id)[-1].event_type == "DECISION_DENIED"


def test_every_decision_needs_a_comment_including_a_second_one(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "REJECT", "Price not agreed.", ANALYST)
    with pytest.raises(ValidationError, match="comment is required"):
        harness.service.decide(invoice.id, "APPROVE", "  ", ANALYST)
    assert len(harness.service.decisions(invoice.id)) == 1


def test_approve_and_override_depend_on_the_engine_outcome(harness):
    rejected = harness.submit(_rejected_payload())
    with pytest.raises(InvalidState, match="Use OVERRIDE"):
        harness.service.decide(rejected.id, "APPROVE", "x", MANAGER)

    held_payload = _held_payload()
    held_payload["header"]["invoice_number"] = read("TEST-0003")  # not a duplicate of the first
    held = harness.submit(held_payload)
    with pytest.raises(InvalidState, match="Use APPROVE"):
        harness.service.decide(held.id, "OVERRIDE", "x", MANAGER)


def test_a_decision_that_changes_nothing_is_refused(harness):
    auto_pay = harness.submit()
    with pytest.raises(InvalidState, match="would change nothing"):
        harness.service.decide(auto_pay.id, "APPROVE", "Approve again.", MANAGER)

    held_payload = _held_payload()
    held_payload["header"]["invoice_number"] = read("TEST-0003")
    held = harness.submit(held_payload)
    harness.service.decide(held.id, "REJECT", "No.", ANALYST)
    with pytest.raises(InvalidState, match="would change nothing"):
        harness.service.decide(held.id, "REJECT", "No again.", ANALYST)
    assert len(harness.service.decisions(held.id)) == 1


def test_invoices_that_are_not_final_cannot_be_decided(harness):
    pending = harness.service.submit_invoice(b"pending", "pending.pdf", INTAKE)
    with pytest.raises(InvalidState, match="cannot be decided on yet"):
        harness.service.decide(pending.id, "REJECT", "x", ANALYST)

    failed = harness.submit(result=failed_result())
    with pytest.raises(InvalidState, match="cannot be decided on yet"):
        harness.service.decide(failed.id, "REJECT", "x", ANALYST)


@pytest.mark.parametrize("decision", ["APPROVE", "OVERRIDE", "REJECT", "REQUEST_CREDIT_NOTE"])
def test_a_document_that_is_not_an_invoice_can_never_be_decided(harness, decision):
    invoice = harness.submit({"document_type": "quotation"})
    with pytest.raises(InvalidState, match="never be approved for payment"):
        harness.service.decide(invoice.id, decision, "Pay it anyway.", MANAGER)
    assert invoice.status == InvoiceStatus.NOT_AN_INVOICE.value


# ---- allowed actions: the same rules, exposed for clients


def _allowed(harness, invoice, user) -> dict:
    return harness.service.allowed_actions(invoice.id, user)


def _decision_names(actions) -> list[str]:
    return [d["decision"] for d in actions["decisions"]]


def test_allowed_actions_follow_the_status_and_the_engine_outcome(harness):
    held = harness.submit(_held_payload())
    assert _decision_names(_allowed(harness, held, ANALYST)) == ["APPROVE", "REJECT", "REQUEST_CREDIT_NOTE"]

    rejected = harness.submit(_rejected_payload())  # duplicate number and no PO: rejected
    assert _decision_names(_allowed(harness, rejected, ANALYST)) == ["OVERRIDE", "REJECT", "REQUEST_CREDIT_NOTE"]

    auto_pay = harness.submit({"header": {"invoice_number": read("TEST-0002")}})
    assert auto_pay.status == InvoiceStatus.AUTO_PAY.value
    assert _decision_names(_allowed(harness, auto_pay, ANALYST)) == ["REJECT", "REQUEST_CREDIT_NOTE"]


def test_allowed_actions_change_after_a_decision(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "REJECT", "No.", ANALYST)
    assert _decision_names(_allowed(harness, invoice, ANALYST)) == ["APPROVE", "REQUEST_CREDIT_NOTE"]
    harness.service.decide(invoice.id, "APPROVE", "Yes.", ANALYST)
    assert _decision_names(_allowed(harness, invoice, ANALYST)) == ["REJECT", "REQUEST_CREDIT_NOTE"]


def test_allowed_actions_take_the_role_into_account(harness):
    invoice = harness.submit(_held_payload())
    assert _allowed(harness, invoice, INTAKE) == {"decisions": [], "can_correct": False, "can_retry": False}
    for reviewer in (ANALYST, MANAGER):
        actions = _allowed(harness, invoice, reviewer)
        assert actions["can_correct"] is True and actions["can_retry"] is False
        assert all(d["allowed"] for d in actions["decisions"])


def test_allowed_actions_apply_the_approval_limit_per_role(harness):
    invoice = harness.submit(_large_held_payload())
    notice = (
        "The invoice total (6,000.00) is above the analyst approval limit (5,000.00). "
        "The AP Manager must decide."
    )
    for_analyst = {d["decision"]: d for d in _allowed(harness, invoice, ANALYST)["decisions"]}
    assert for_analyst["APPROVE"] == {
        "decision": "APPROVE", "allowed": False, "required_role": "AP_MANAGER", "notice": notice,
        "warning": None,
    }
    # Refusing payment is not limited.
    assert for_analyst["REJECT"]["allowed"] is True and for_analyst["REJECT"]["notice"] is None
    assert for_analyst["REQUEST_CREDIT_NOTE"]["allowed"] is True

    for_manager = {d["decision"]: d for d in _allowed(harness, invoice, MANAGER)["decisions"]}
    assert for_manager["APPROVE"]["allowed"] is True
    assert for_manager["APPROVE"]["required_role"] == "AP_MANAGER"
    assert for_manager["APPROVE"]["notice"] == notice


def test_allowed_actions_require_the_manager_when_the_total_is_unreadable(harness):
    payload = _held_payload()
    payload["header"]["total_amount"] = None
    invoice = harness.submit(payload)
    approve = _allowed(harness, invoice, ANALYST)["decisions"][0]
    assert approve["allowed"] is False and approve["required_role"] == "AP_MANAGER"
    assert "unknown" in approve["notice"]
    assert _allowed(harness, invoice, MANAGER)["decisions"][0]["allowed"] is True


def test_allowed_actions_apply_the_limit_to_overrides_too(harness):
    invoice = harness.submit(_rejected_payload(total="9000.00"))
    override = _allowed(harness, invoice, ANALYST)["decisions"][0]
    assert override["decision"] == "OVERRIDE" and override["allowed"] is False
    assert _allowed(harness, invoice, MANAGER)["decisions"][0]["allowed"] is True


def test_allowed_actions_use_the_limit_in_force(harness):
    invoice = harness.submit(_large_held_payload())
    assert _allowed(harness, invoice, ANALYST)["decisions"][0]["allowed"] is False
    harness.service.update_parameters({"analyst_approval_limit": "10000"}, "Raised.", MANAGER)
    raised = _allowed(harness, invoice, ANALYST)["decisions"][0]
    assert raised["allowed"] is True and raised["required_role"] == "AP_ANALYST" and raised["notice"] is None


def test_allowed_actions_for_documents_that_cannot_be_decided(harness):
    quotation = harness.submit({"document_type": "quotation"})
    assert _allowed(harness, quotation, MANAGER) == {"decisions": [], "can_correct": True, "can_retry": False}

    failed = harness.submit(result=failed_result())
    assert _allowed(harness, failed, ANALYST) == {"decisions": [], "can_correct": False, "can_retry": True}

    pending = harness.service.submit_invoice(b"pending", "pending.pdf", INTAKE)
    assert _allowed(harness, pending, ANALYST) == {"decisions": [], "can_correct": False, "can_retry": False}


def test_allowed_actions_after_a_correction_wait_for_a_decision(harness):
    invoice = harness.submit({"lines": [_line(quantity=not_read("illegible"))]})
    harness.service.correct_extraction(
        invoice.id, {"lines": [{"line_number": 1, "quantity": "4"}]}, "Read from paper.", ANALYST
    )
    assert _decision_names(_allowed(harness, invoice, ANALYST)) == ["APPROVE", "REJECT", "REQUEST_CREDIT_NOTE"]


def test_every_allowed_decision_is_accepted_and_every_other_one_is_refused(harness):
    """allowed_actions and decide() must never disagree."""
    payloads = [_held_payload(), _large_held_payload(), _rejected_payload(), None]
    for number, payload in enumerate(payloads):
        for user in (ANALYST, MANAGER):
            for decision in ("APPROVE", "OVERRIDE", "REJECT", "REQUEST_CREDIT_NOTE"):
                data = {"header": {}} if payload is None else {k: dict(v) if isinstance(v, dict) else v for k, v in payload.items()}
                data.setdefault("header", {})
                data["header"]["invoice_number"] = read(f"N-{number}-{user.username}-{decision}")
                invoice = harness.submit(data)
                offered = {d["decision"]: d["allowed"] for d in _allowed(harness, invoice, user)["decisions"]}
                try:
                    harness.service.decide(invoice.id, decision, "Checking consistency.", user)
                    accepted = True
                except (InvalidState, PermissionDenied):
                    accepted = False
                assert accepted == offered.get(decision, False), (payload, user.username, decision)


# ---- parameters (DL-11)


def test_parameters_are_seeded_once_from_settings(harness):
    first = harness.service.current_parameters()
    second = harness.service.current_parameters()
    assert first.version == second.version == 1
    assert first.price_tolerance_pct == Decimal("0.02")
    assert first.analyst_approval_limit == Decimal("5000")
    assert len(list(harness.session.scalars(select(RuleParametersRow)))) == 1


def test_only_the_manager_changes_parameters(harness):
    for user in (INTAKE, ANALYST):
        with pytest.raises(PermissionDenied):
            harness.service.update_parameters({"price_tolerance_pct": "0.5"}, "Wider.", user)
    assert harness.service.current_parameters().version == 1


def test_parameter_change_is_validated(harness):
    update = harness.service.update_parameters
    with pytest.raises(ValidationError, match="comment"):
        update({"price_tolerance_pct": "0.5"}, " ", MANAGER)
    with pytest.raises(ValidationError, match="Unknown parameter"):
        update({"vat_rate": "0.21"}, "x", MANAGER)
    with pytest.raises(ValidationError, match="cannot be negative"):
        update({"price_tolerance_pct": "-0.1"}, "x", MANAGER)
    with pytest.raises(ValidationError, match="must be a number"):
        update({"analyst_approval_limit": "lots"}, "x", MANAGER)
    with pytest.raises(ValidationError, match="Spec rules cannot be switched"):
        update({"enabled_assumption_rules": ["A1", "R5"]}, "x", MANAGER)
    with pytest.raises(ValidationError, match="No parameter"):
        update({}, "x", MANAGER)


def test_new_parameters_apply_to_new_evaluations_only(harness):
    old = harness.submit(_held_payload())  # 30.00 against 25.00 is 20%: held at 2%
    old_evaluation = harness.service.latest_evaluation(old.id)

    new = harness.service.update_parameters(
        {"price_tolerance_pct": "0.25"}, "Temporary tolerance agreed with the CFO.", MANAGER
    )
    assert new.version == 2 and new.changed_by == "max.manager"

    # The earlier evaluation and its invoice are exactly as they were.
    assert old.status == InvoiceStatus.HELD_FOR_REVIEW.value
    assert old_evaluation.outcome == "HELD_FOR_REVIEW"
    assert old_evaluation.parameters_snapshot["version"] == 1
    assert Decimal(old_evaluation.parameters_snapshot["price_tolerance_pct"]) == Decimal("0.02")

    payload = _held_payload()
    payload["header"]["invoice_number"] = read("TEST-0002")
    fresh = harness.submit(payload)
    assert fresh.status == InvoiceStatus.AUTO_PAY.value
    snapshot = harness.service.latest_evaluation(fresh.id).parameters_snapshot
    assert snapshot["version"] == 2 and Decimal(snapshot["price_tolerance_pct"]) == Decimal("0.25")

    event = list(harness.session.scalars(
        select(AuditEventRow).where(AuditEventRow.event_type == "PARAMETERS_UPDATED")
    ))[0]
    assert event.invoice_id is None and event.actor == "max.manager"
    assert event.details["version"] == 2


def test_assumption_rules_can_be_switched_off_by_the_manager(harness):
    harness.service.update_parameters(
        {"enabled_assumption_rules": ["A1", "A2"]}, "A4 off while invoices arrive in two currencies.", MANAGER
    )
    invoice = harness.submit({"header": {"currency": "USD"}})  # the PO is in EUR
    assert invoice.status == InvoiceStatus.AUTO_PAY.value  # A4 no longer holds it


def test_fixed_rules_cannot_be_switched_off(harness):
    """A6, A7 and A9 always apply (DL-18, DL-35, DL-78)."""
    current = harness.service.current_parameters()
    assert not {"A6", "A7", "A9"} & current.enabled_assumption_rules

    for code in ("A6", "A7", "A9"):
        with pytest.raises(ValidationError, match="neither can A6, A7 or A9"):
            harness.service.update_parameters({"enabled_assumption_rules": ["A1", code]}, "Trying.", MANAGER)
    assert harness.service.current_parameters().version == 1


def test_with_every_assumption_off_missing_data_is_still_held_or_rejected(harness):
    harness.service.update_parameters({"enabled_assumption_rules": []}, "Every assumption off.", MANAGER)

    def submit(number: str, **payload):
        payload.setdefault("header", {})
        payload["header"].setdefault("invoice_number", read(number))
        invoice = harness.submit(payload)
        reasons = harness.service.latest_evaluation(invoice.id).reasons
        return invoice.status, [(r["code"], r["source"], r["decision_log_ref"]) for r in reasons]

    assert submit("M-1", lines=[_line(quantity=not_read("illegible", suggested="4"))]) == (
        "HELD_FOR_REVIEW", [("A6", "CONFIRMED", "DL-18")],
    )
    assert submit("M-2", header={"po_references": {"values": [], "status": "illegible", "suggested_values": ["PO-2026-0002"]}}) == (
        "HELD_FOR_REVIEW", [("A7", "CONFIRMED", "DL-18")],
    )
    assert submit("M-3", header={"invoice_number": not_read("illegible")}) == (
        "HELD_FOR_REVIEW", [("A9", "CONFIRMED", "DL-18")],
    )
    assert submit("M-4", header={"invoice_number": not_read("absent")}) == (
        "HELD_FOR_REVIEW", [("A9", "ASSUMPTION", "DL-35")],
    )
    assert submit("M-5", header={"invoice_date": not_read("illegible")}) == (
        "HELD_FOR_REVIEW", [("A9", "CONFIRMED", "DL-18")],
    )
    assert submit("M-6", header={"invoice_date": not_read("absent")}) == (
        "HELD_FOR_REVIEW", [("A9", "ASSUMPTION", "DL-35")],
    )
    status, reasons = submit("M-7", header={"po_references": {"values": [], "status": "absent", "suggested_values": []}})
    assert status == "REJECTED" and ("R1", "SPEC", None) in reasons
    # The control: with the same parameters, a complete invoice is paid.
    assert submit("M-8")[0] == "AUTO_PAY"


def test_a_parameter_version_stored_when_those_rules_were_switchable_is_read_without_them(harness):
    from app.db.invoice_tables import RuleParametersRow as Row

    harness.service.current_parameters()
    row = harness.session.scalars(select(Row)).one()
    row.enabled_assumption_rules = ["A1", "A6", "A7", "A9"]
    harness.session.commit()
    assert harness.service.current_parameters().enabled_assumption_rules == frozenset({"A1"})


def test_correction_event_records_each_change_with_what_it_was_before(harness):
    invoice = harness.submit({"lines": [_line(quantity=not_read("illegible"))]})
    harness.service.correct_extraction(
        invoice.id, {"lines": [{"line_number": 1, "quantity": "4"}]}, "Read from paper.", ANALYST
    )
    corrected = next(e for e in harness.service.audit_trail(invoice.id) if e.event_type == "EXTRACTION_CORRECTED")
    assert corrected.details["changes"] == [
        {
            "scope": "line",
            "line_number": 1,
            "field": "quantity",
            "before": {"value": None, "status": "illegible"},
            "after": {"value": "4", "status": "read"},
        }
    ]


def test_evaluation_event_records_the_reasons_in_plain_language(harness):
    invoice = harness.submit(_held_payload())
    evaluated = next(e for e in harness.service.audit_trail(invoice.id) if e.event_type == "INVOICE_EVALUATED")
    assert evaluated.details["reason_codes"] == ["H2"]
    assert evaluated.details["reasons"][0]["code"] == "H2"
    assert "unit price 30.00 differs from PO price 25.00" in evaluated.details["reasons"][0]["message"]


def test_advice_when_no_line_is_matched_to_a_purchase_order(harness):
    rejected = harness.submit(_rejected_payload())  # no PO on the document
    override = _allowed(harness, rejected, ANALYST)["decisions"][0]
    assert override["decision"] == "OVERRIDE" and override["allowed"] is True
    assert override["warning"].startswith("No purchase order is associated with this invoice")
    # Advice only: the decision is still accepted.
    harness.service.decide(rejected.id, "OVERRIDE", "PO confirmed by purchasing.", ANALYST)
    assert rejected.status == InvoiceStatus.OVERRIDDEN.value

    held_payload = _held_payload()
    held_payload["header"]["invoice_number"] = read("TEST-0009")
    held = harness.submit(held_payload)
    assert all(d["warning"] is None for d in _allowed(harness, held, ANALYST)["decisions"])


def test_the_approval_limit_in_force_is_used_for_decisions(harness):
    invoice = harness.submit(_large_held_payload())
    harness.service.update_parameters({"analyst_approval_limit": "10000"}, "Raised.", MANAGER)
    harness.service.decide(invoice.id, "APPROVE", "Within the new limit.", ANALYST)
    assert invoice.status == InvoiceStatus.APPROVED_FOR_PAYMENT.value


# ---- audit trail


def test_every_status_change_writes_an_audit_event(harness):
    invoice = harness.submit(_held_payload())
    harness.service.decide(invoice.id, "APPROVE", "Agreed.", ANALYST)
    events = harness.service.audit_trail(invoice.id)
    assert [e.event_type for e in events] == [
        "INVOICE_SUBMITTED",
        "EXTRACTION_SUCCEEDED",
        "INVOICE_EVALUATED",
        "HUMAN_DECISION_RECORDED",
    ]
    assert [e.actor for e in events] == ["ines.intake", "system", "system", "ana.analyst"]
    assert events[2].details["outcome"] == "HELD_FOR_REVIEW"
    assert events[2].details["reason_codes"] == ["H2"]
    assert events[3].details["previous_outcome"] == "HELD_FOR_REVIEW"
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_audit_events_cannot_be_updated_or_deleted(harness):
    invoice = harness.submit()
    session = harness.session
    event = harness.service.audit_trail(invoice.id)[0]
    total = len(list(session.scalars(select(AuditEventRow))))

    event.actor = "someone.else"
    with pytest.raises(AuditTrailIsAppendOnly):
        session.commit()
    session.rollback()

    session.delete(event)
    with pytest.raises(AuditTrailIsAppendOnly):
        session.commit()
    session.rollback()

    with pytest.raises(AuditTrailIsAppendOnly):
        session.execute(update(AuditEventRow).values(actor="someone.else"))
    with pytest.raises(AuditTrailIsAppendOnly):
        session.execute(delete(AuditEventRow))
    session.rollback()

    events = list(session.scalars(select(AuditEventRow)))
    assert len(events) == total
    assert harness.service.audit_trail(invoice.id)[0].actor == "ines.intake"


def test_no_api_key_material_is_stored(harness, session):
    invoice = harness.submit()
    version = harness.service.latest_extraction(invoice.id)
    stored = str(version.payload) + str(version.raw_response)
    assert "api-key" not in stored.lower() and "api_key" not in stored.lower()
