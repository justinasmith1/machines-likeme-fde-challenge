"""HTTP API over the services. Extraction is stubbed; nothing reaches the model or the network."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app
from app.storage.files import LocalFileStore
from db_builders import StubExtractor, extraction_result, failed_result, seed_reference
from extraction_builders import make_payload, not_read, read

INTAKE = {"X-Dev-User": "ines.intake", "X-Dev-Role": "INTAKE"}
ANALYST = {"X-Dev-User": "ana.analyst", "X-Dev-Role": "AP_ANALYST"}
MANAGER = {"X-Dev-User": "max.manager", "X-Dev-Role": "AP_MANAGER"}
ROLES = {"INTAKE": INTAKE, "AP_ANALYST": ANALYST, "AP_MANAGER": MANAGER}
PDF = ("invoice.pdf", b"%PDF-1.7 fake content", "application/pdf")


class Api:
    def __init__(self, tmp_path, **settings):
        self.engine = make_engine("sqlite://")
        init_db(self.engine)
        with make_session_factory(self.engine)() as session:
            seed_reference(session)
        self.extractor = StubExtractor()
        # Like a developer's .env: APP_ENV=development is set explicitly.
        self.settings = Settings(_env_file=None, **{"app_env": "development", **settings})
        self.app = create_app(
            self.settings,
            engine=self.engine,
            file_store=LocalFileStore(tmp_path / "uploads"),
            extractor=self.extractor,
        )

    def upload(self, client, payload=None, *, result=None, headers=INTAKE, file=PDF):
        """Upload one document; the background task extracts and evaluates it."""
        self.extractor.queue.append(result or extraction_result(payload))
        response = client.post("/api/invoices", files={"file": file}, headers=headers)
        assert response.status_code == 202, response.text
        return response.json()["id"]


@pytest.fixture
def api(tmp_path):
    return Api(tmp_path)


@pytest.fixture
def client(api):
    with TestClient(api.app) as client:
        yield client


def _line(**overrides) -> dict:
    line = make_payload()["lines"][0]
    line.update(overrides)
    return line


def _held_payload() -> dict:
    """Unit price 30.00 against 25.00 on the PO: held under H2. Total 141.00."""
    return {
        "header": {"subtotal": "120.00", "tax_amount": "21.00", "total_amount": "141.00"},
        "lines": [_line(unit_price=read("30.00"), line_total="120.00")],
    }


def _large_held_payload() -> dict:
    """Held for price, total 6,000.00: above the 5,000 analyst limit."""
    return {
        "header": {"subtotal": "6000.00", "tax_amount": "0.00", "total_amount": "6000.00"},
        "lines": [_line(unit_price=read("1500.00"), line_total="6000.00")],
    }


def _error(response) -> dict:
    return response.json()["error"]


# ---- authentication


def test_me_returns_the_current_user_and_role(client):
    assert client.get("/api/me", headers=ANALYST).json() == {
        "username": "ana.analyst",
        "role": "AP_ANALYST",
    }


def test_request_without_development_headers_is_unauthenticated(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHENTICATED"


def test_unknown_role_is_unauthenticated(client):
    response = client.get("/api/me", headers={"X-Dev-User": "x", "X-Dev-Role": "ADMIN"})
    assert response.status_code == 401 and "Unknown role" in _error(response)["message"]


def test_app_env_defaults_to_production():
    assert Settings(_env_file=None).app_env == "production"


def test_without_app_env_configured_the_development_headers_are_ignored(tmp_path):
    engine = make_engine("sqlite://")
    app = create_app(
        Settings(_env_file=None),  # nothing configured
        engine=engine,
        file_store=LocalFileStore(tmp_path),
        extractor=StubExtractor(),
    )
    with TestClient(app) as client:
        for headers in (INTAKE, ANALYST, MANAGER):
            response = client.get("/api/me", headers=headers)
            assert response.status_code == 401
            assert _error(response)["code"] == "UNAUTHENTICATED"
        assert client.get("/api/invoices", headers=MANAGER).status_code == 401
        assert client.post("/api/invoices", files={"file": PDF}, headers=MANAGER).status_code == 401


@pytest.mark.parametrize("app_env", ["production", "staging", ""])
def test_development_headers_are_ignored_outside_development(tmp_path, app_env):
    api = Api(tmp_path, app_env=app_env)
    with TestClient(api.app) as client:
        for path in ("/api/me", "/api/invoices", "/api/review-queue", "/api/parameters"):
            response = client.get(path, headers=MANAGER)
            assert response.status_code == 401, path
            assert _error(response)["code"] == "UNAUTHENTICATED"
        upload = client.post("/api/invoices", files={"file": PDF}, headers=MANAGER)
        assert upload.status_code == 401
        assert client.get("/health").status_code == 200  # the health check stays open


# ---- permissions by role


@pytest.mark.parametrize(
    ("method", "path", "body", "allowed"),
    [
        ("get", "/api/me", None, {"INTAKE", "AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/invoices", None, {"INTAKE", "AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/invoices/1", None, {"INTAKE", "AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/invoices/1/document", None, {"INTAKE", "AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/invoices/1/audit", None, {"AP_MANAGER"}),
        ("get", "/api/review-queue", None, {"AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/parameters", None, {"AP_ANALYST", "AP_MANAGER"}),
        ("get", "/api/parameters/history", None, {"AP_MANAGER"}),
        ("put", "/api/parameters", {"values": {"rounding_tolerance": "0.02"}, "comment": "c"}, {"AP_MANAGER"}),
        ("post", "/api/invoices/1/corrections",
         {"corrections": {"header": {"due_date": "2026-04-01"}}, "comment": "c"}, {"AP_ANALYST", "AP_MANAGER"}),
        ("post", "/api/invoices/1/decisions", {"decision": "REJECT", "comment": "c"}, {"AP_ANALYST", "AP_MANAGER"}),
    ],
)
def test_permissions_by_role(api, client, method, path, body, allowed):
    for role, headers in ROLES.items():
        api.upload(client, _held_payload())  # invoice 1 exists; later uploads are duplicates
        kwargs = {"headers": headers} if body is None else {"headers": headers, "json": body}
        response = getattr(client, method)(path, **kwargs)
        if role in allowed:
            # A repeated decision on the same invoice is a 409: a state error, not a
            # permission error. Allowed means "not refused for who you are".
            assert response.status_code in (200, 409), (role, response.text)
        else:
            assert response.status_code == 403, (role, response.text)
            assert _error(response)["code"] == "PERMISSION_DENIED"


def test_retry_is_for_reviewers_only(api, client):
    invoice_id = api.upload(client, result=failed_result())
    assert client.post(f"/api/invoices/{invoice_id}/retry", headers=INTAKE).status_code == 403
    api.extractor.queue.append(extraction_result())
    response = client.post(f"/api/invoices/{invoice_id}/retry", headers=ANALYST)
    assert response.status_code == 202
    assert client.get(f"/api/invoices/{invoice_id}", headers=ANALYST).json()["invoice"]["status"] == "AUTO_PAY"


def test_all_roles_can_upload(api, client):
    for headers in ROLES.values():
        api.upload(client, headers=headers)


def test_intake_decision_attempt_is_refused_and_audited(api, client):
    invoice_id = api.upload(client, _held_payload())
    response = client.post(
        f"/api/invoices/{invoice_id}/decisions", json={"decision": "APPROVE", "comment": "ok"}, headers=INTAKE
    )
    assert response.status_code == 403
    events = client.get(f"/api/invoices/{invoice_id}/audit", headers=MANAGER).json()["events"]
    assert events[-1]["event_type"] == "DECISION_DENIED" and events[-1]["actor"] == "ines.intake"


# ---- full flow


def test_upload_is_processed_in_the_background_then_decided_and_audited(api, client):
    api.extractor.queue.append(extraction_result(_held_payload()))
    uploaded = client.post("/api/invoices", files={"file": PDF}, headers=INTAKE)
    assert uploaded.status_code == 202
    assert uploaded.json() == {"id": 1, "filename": "invoice.pdf", "status": "PENDING_EXTRACTION"}
    assert api.extractor.calls == ["invoice.pdf"]  # the background task ran

    detail = client.get("/api/invoices/1", headers=ANALYST).json()
    assert detail["invoice"]["status"] == "HELD_FOR_REVIEW"
    assert detail["invoice"]["uploaded_by"] == "ines.intake"
    assert detail["invoice"]["total"] == "141.00"
    reason = detail["evaluation"]["reasons"][0]
    assert (reason["code"], reason["source"], reason["decision_log_ref"]) == ("H2", "SPEC", None)
    assert "30.00" in reason["message"]
    assert detail["evaluation"]["parameters_snapshot"]["version"] == 1
    assert detail["evaluation"]["matching"]["supplier_method"] == "TAX_ID"
    assert detail["extraction"]["lines"][0]["po_line_mapping"] == {
        "po_number": "PO-2026-0002", "po_line_number": 1, "method": "PRINTED_PO_LINE",
    }
    assert detail["extraction_versions"] == [
        {"version": 1, "source": "MODEL", "created_by": "system", "created_at": detail["extraction"]["created_at"],
         "comment": None, "model": "test-deployment", "prompt_version": detail["extraction"]["prompt_version"]}
    ]
    assert client.get("/api/review-queue", headers=ANALYST).json()["invoices"][0]["id"] == 1

    decided = client.post(
        "/api/invoices/1/decisions",
        json={"decision": "APPROVE", "comment": "Price agreed with purchasing."},
        headers=ANALYST,
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["invoice"]["status"] == "APPROVED_FOR_PAYMENT"
    assert body["decisions"][0]["decided_by"] == "ana.analyst"
    assert body["decisions"][0]["previous_status"] == "HELD_FOR_REVIEW"
    assert body["decisions"][0]["superseded"] is False
    assert body["evaluation"]["outcome"] == "HELD_FOR_REVIEW"  # the engine result is kept
    assert client.get("/api/review-queue", headers=ANALYST).json()["invoices"] == []

    events = client.get("/api/invoices/1/audit", headers=MANAGER).json()["events"]
    assert [(e["event_type"], e["actor"]) for e in events] == [
        ("INVOICE_SUBMITTED", "ines.intake"),
        ("EXTRACTION_SUCCEEDED", "system"),
        ("INVOICE_EVALUATED", "system"),
        ("HUMAN_DECISION_RECORDED", "ana.analyst"),
    ]
    assert events[-1]["details"]["comment"] == "Price agreed with purchasing."


def test_decision_without_a_comment_is_a_validation_error(api, client):
    invoice_id = api.upload(client, _held_payload())
    for body in ({"decision": "APPROVE"}, {"decision": "APPROVE", "comment": "  "}):
        response = client.post(f"/api/invoices/{invoice_id}/decisions", json=body, headers=ANALYST)
        assert response.status_code == 422
        assert _error(response)["code"] == "VALIDATION_ERROR"
        assert "comment is required" in _error(response)["message"]


def test_analyst_above_the_limit_is_forbidden_and_the_manager_is_not(api, client):
    invoice_id = api.upload(client, _large_held_payload())
    body = {"decision": "APPROVE", "comment": "Approved."}

    refused = client.post(f"/api/invoices/{invoice_id}/decisions", json=body, headers=ANALYST)
    assert refused.status_code == 403
    assert _error(refused)["code"] == "PERMISSION_DENIED"
    assert "6,000.00" in _error(refused)["message"] and "AP Manager" in _error(refused)["message"]

    accepted = client.post(f"/api/invoices/{invoice_id}/decisions", json=body, headers=MANAGER)
    assert accepted.status_code == 200
    assert accepted.json()["invoice"]["status"] == "APPROVED_FOR_PAYMENT"
    assert accepted.json()["decisions"][0]["required_role"] == "AP_MANAGER"

    events = client.get(f"/api/invoices/{invoice_id}/audit", headers=MANAGER).json()["events"]
    assert [e["event_type"] for e in events][-2:] == ["DECISION_DENIED", "HUMAN_DECISION_RECORDED"]


def test_correction_holds_with_the_reason_then_approval_releases_payment(api, client):
    illegible = {"lines": [_line(quantity=not_read("illegible", suggested="4"))]}
    invoice_id = api.upload(client, illegible)

    before = client.get(f"/api/invoices/{invoice_id}", headers=ANALYST).json()
    quantity = before["extraction"]["lines"][0]["quantity"]
    assert quantity["value"] is None and quantity["status"] == "illegible"
    assert quantity["suggestion"]["value"] == "4" and quantity["suggestion"]["is_suggestion"] is True
    assert "not a value read from the document" in quantity["suggestion"]["note"]
    assert before["evaluation"]["reasons"][0]["code"] == "A6"
    assert before["evaluation"]["reasons"][0]["source"] == "ASSUMPTION"
    assert before["evaluation"]["reasons"][0]["decision_log_ref"] == "DL-18"

    corrected = client.post(
        f"/api/invoices/{invoice_id}/corrections",
        json={"corrections": {"lines": [{"line_number": 1, "quantity": "4"}]}, "comment": "Read from the paper copy."},
        headers=ANALYST,
    )
    assert corrected.status_code == 200
    body = corrected.json()
    assert body["invoice"]["status"] == "HELD_FOR_REVIEW"
    assert body["invoice"]["status_reason"] == (
        "Extracted data was corrected by ana.analyst; a human decision is required before payment."
    )
    assert body["evaluation"]["outcome"] == "AUTO_PAY" and body["evaluation"]["reasons"] == []
    assert body["extraction"]["lines"][0]["quantity"] == {"value": "4", "status": "read", "suggestion": None}
    assert [(v["version"], v["source"]) for v in body["extraction_versions"]] == [
        (1, "MODEL"), (2, "HUMAN_CORRECTION"),
    ]

    approved = client.post(
        f"/api/invoices/{invoice_id}/decisions",
        json={"decision": "APPROVE", "comment": "Checked against the delivery note."},
        headers=ANALYST,
    )
    assert approved.status_code == 200
    assert approved.json()["invoice"]["status"] == "APPROVED_FOR_PAYMENT"
    assert approved.json()["invoice"]["status_reason"] is None


def test_superseded_decisions_are_returned(api, client):
    invoice_id = api.upload(client, _held_payload())
    url = f"/api/invoices/{invoice_id}/decisions"
    client.post(url, json={"decision": "REJECT", "comment": "No."}, headers=ANALYST)
    client.post(url, json={"decision": "APPROVE", "comment": "Yes after all."}, headers=MANAGER)
    decisions = client.get(f"/api/invoices/{invoice_id}", headers=ANALYST).json()["decisions"]
    assert [(d["decision"], d["superseded"], d["superseded_reason"]) for d in decisions] == [
        ("REJECT", True, "NEW_DECISION"),
        ("APPROVE", False, None),
    ]


# ---- what intake sees


def test_intake_sees_status_and_plain_reasons_and_nothing_internal(api, client):
    invoice_id = api.upload(client, _held_payload())
    body = client.get(f"/api/invoices/{invoice_id}", headers=INTAKE).json()

    assert set(body) == {"id", "filename", "uploaded_at", "status", "reasons"}
    assert body["filename"] == "invoice.pdf" and body["status"] == "HELD_FOR_REVIEW"
    assert len(body["reasons"]) == 1 and "unit price 30.00 differs from PO price 25.00" in body["reasons"][0]
    text = str(body)
    for internal in ("H2", "SPEC", "ASSUMPTION", "DL-", "parameters", "tolerance_pct", "uploaded_by", "audit", "matching"):
        assert internal not in text, internal

    listed = client.get("/api/invoices", headers=INTAKE).json()["invoices"]
    assert set(listed[0]) == {"id", "filename", "uploaded_at", "status", "reasons"}


def test_intake_does_not_see_reviewer_comments_or_who_corrected(api, client):
    invoice_id = api.upload(client, _held_payload())
    client.post(
        f"/api/invoices/{invoice_id}/corrections",
        json={"corrections": {"header": {"due_date": "2026-04-01"}}, "comment": "internal note"},
        headers=ANALYST,
    )
    held = client.get(f"/api/invoices/{invoice_id}", headers=INTAKE).json()
    assert held["reasons"] == ["The extracted data was corrected by a reviewer; a decision is pending."]

    client.post(
        f"/api/invoices/{invoice_id}/decisions",
        json={"decision": "REJECT", "comment": "supplier is unreliable"},
        headers=ANALYST,
    )
    rejected = client.get(f"/api/invoices/{invoice_id}", headers=INTAKE).json()
    assert rejected["status"] == "REJECTED_BY_REVIEWER" and rejected["reasons"] == []
    assert "ana.analyst" not in str(held) + str(rejected)
    assert "unreliable" not in str(rejected) and "internal note" not in str(held)


def test_intake_sees_why_a_document_is_not_an_invoice_or_failed(api, client):
    quotation = api.upload(client, {"document_type": "quotation", "classification_reason": "Titled Quotation."})
    body = client.get(f"/api/invoices/{quotation}", headers=INTAKE).json()
    assert body["status"] == "NOT_AN_INVOICE"
    assert body["reasons"] == ["Received, not an invoice: quotation (Titled Quotation.)"]

    failed = api.upload(client, result=failed_result())
    body = client.get(f"/api/invoices/{failed}", headers=INTAKE).json()
    assert body["status"] == "EXTRACTION_FAILED"
    assert body["reasons"] == ["The model service returned HTTP 503."]


# ---- uploads and documents


def test_file_above_the_limit_is_413(tmp_path):
    api = Api(tmp_path, max_upload_mb=1)
    with TestClient(api.app) as client:
        big = ("big.pdf", b"%PDF" + b"0" * (1024 * 1024 + 1), "application/pdf")
        response = client.post("/api/invoices", files={"file": big}, headers=INTAKE)
        assert response.status_code == 413
        assert _error(response) == {"code": "FILE_TOO_LARGE", "message": "The file is larger than the 1 MB limit."}
        assert client.get("/api/invoices", headers=ANALYST).json()["invoices"] == []


def test_unsupported_file_type_is_refused_with_a_clear_message(client):
    word = ("invoice.docx", b"PK fake", "application/octet-stream")
    response = client.post("/api/invoices", files={"file": word}, headers=INTAKE)
    assert response.status_code == 422
    assert _error(response) == {
        "code": "UNSUPPORTED_DOCUMENT_TYPE",
        "message": "File type '.docx' is not supported. Upload a PDF, PNG or JPG.",
    }


def test_empty_file_and_missing_file_are_validation_errors(client):
    empty = client.post("/api/invoices", files={"file": ("empty.pdf", b"", "application/pdf")}, headers=INTAKE)
    assert empty.status_code == 422 and _error(empty)["message"] == "The file is empty."
    missing = client.post("/api/invoices", headers=INTAKE)
    assert missing.status_code == 422 and _error(missing)["code"] == "VALIDATION_ERROR"


def test_document_endpoint_returns_the_original_file(api, client):
    invoice_id = api.upload(client)
    response = client.get(f"/api/invoices/{invoice_id}/document", headers=INTAKE)
    assert response.status_code == 200
    assert response.content == PDF[1]
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="invoice.pdf"' in response.headers["content-disposition"]


# ---- lists, errors, parameters


def test_invoice_list_can_be_filtered_by_status(api, client):
    api.upload(client)
    api.upload(client, _held_payload())  # same number as the first: rejected as a duplicate
    everything = client.get("/api/invoices", headers=ANALYST).json()["invoices"]
    assert [i["status"] for i in everything] == ["REJECTED", "AUTO_PAY"]  # newest first
    auto_pay = client.get("/api/invoices?status=AUTO_PAY", headers=ANALYST).json()["invoices"]
    assert [i["id"] for i in auto_pay] == [1]
    unknown = client.get("/api/invoices?status=PAID", headers=ANALYST)
    assert unknown.status_code == 422 and "Unknown status" in _error(unknown)["message"]


def test_review_queue_lists_held_invoices_oldest_first(api, client):
    first = api.upload(client, _held_payload())
    second_payload = _held_payload()
    second_payload["header"]["invoice_number"] = read("TEST-0002")
    second = api.upload(client, second_payload)
    api.upload(client, {"header": {"invoice_number": read("TEST-0003")}})  # auto-pay, not queued

    queue = client.get("/api/review-queue", headers=ANALYST).json()["invoices"]
    assert [i["id"] for i in queue] == [first, second]
    client.post(f"/api/invoices/{first}/decisions", json={"decision": "REJECT", "comment": "No."}, headers=ANALYST)
    assert [i["id"] for i in client.get("/api/review-queue", headers=MANAGER).json()["invoices"]] == [second]


def test_service_errors_map_to_http_status_codes(api, client):
    missing = client.get("/api/invoices/999", headers=ANALYST)
    assert missing.status_code == 404 and _error(missing)["code"] == "NOT_FOUND"

    auto_pay = api.upload(client)
    conflict = client.post(
        f"/api/invoices/{auto_pay}/decisions", json={"decision": "APPROVE", "comment": "Again."}, headers=MANAGER
    )
    assert conflict.status_code == 409 and _error(conflict)["code"] == "INVALID_STATE"
    assert client.post(f"/api/invoices/{auto_pay}/retry", headers=ANALYST).status_code == 409

    invalid = client.post(
        f"/api/invoices/{auto_pay}/corrections",
        json={"corrections": {"header": {"total_amount": "abc"}}, "comment": "Fix."},
        headers=ANALYST,
    )
    assert invalid.status_code == 422 and _error(invalid)["code"] == "VALIDATION_ERROR"
    assert client.get("/api/nothing-here", headers=ANALYST).status_code == 404


def test_not_an_invoice_can_never_be_approved_through_the_api(api, client):
    invoice_id = api.upload(client, {"document_type": "quotation"})
    response = client.post(
        f"/api/invoices/{invoice_id}/decisions", json={"decision": "APPROVE", "comment": "Pay."}, headers=MANAGER
    )
    assert response.status_code == 409
    assert "never be approved for payment" in _error(response)["message"]


def test_parameters_are_seeded_on_startup_and_versioned(client):
    current = client.get("/api/parameters", headers=ANALYST).json()
    assert current["version"] == 1
    assert current["price_tolerance_pct"] == "0.02" and current["analyst_approval_limit"] == "5000.00"
    assert current["enabled_assumption_rules"] == [f"A{n}" for n in range(1, 11)]

    updated = client.put(
        "/api/parameters",
        json={"values": {"price_tolerance_pct": "0.05"}, "comment": "Agreed with the CFO."},
        headers=MANAGER,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2 and updated.json()["price_tolerance_pct"] == "0.05"
    assert updated.json()["changed_by"] == "max.manager"

    history = client.get("/api/parameters/history", headers=MANAGER).json()["versions"]
    assert [(v["version"], v["comment"]) for v in history] == [
        (2, "Agreed with the CFO."),
        (1, "Initial values from configuration."),
    ]

    no_comment = client.put("/api/parameters", json={"values": {"price_tolerance_pct": "0.1"}}, headers=MANAGER)
    assert no_comment.status_code == 422


def test_new_parameters_do_not_change_an_earlier_evaluation(api, client):
    invoice_id = api.upload(client, _held_payload())
    client.put(
        "/api/parameters", json={"values": {"price_tolerance_pct": "0.5"}, "comment": "Wider."}, headers=MANAGER
    )
    detail = client.get(f"/api/invoices/{invoice_id}", headers=ANALYST).json()
    assert detail["invoice"]["status"] == "HELD_FOR_REVIEW"
    assert detail["evaluation"]["parameters_snapshot"]["version"] == 1


# ---- cross-cutting


def test_cors_allows_only_the_configured_frontend(tmp_path):
    api = Api(tmp_path, frontend_origin="https://ap.example.test")
    with TestClient(api.app) as client:
        allowed = client.get("/api/me", headers={**ANALYST, "Origin": "https://ap.example.test"})
        assert allowed.headers["access-control-allow-origin"] == "https://ap.example.test"
        other = client.get("/api/me", headers={**ANALYST, "Origin": "https://evil.example.test"})
        assert "access-control-allow-origin" not in other.headers
        preflight = client.options(
            "/api/invoices/1/decisions",
            headers={
                "Origin": "https://ap.example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-dev-user,x-dev-role,content-type",
            },
        )
        assert preflight.status_code == 200


def test_startup_does_not_load_reference_data(tmp_path):
    engine = make_engine("sqlite://")
    app = create_app(
        Settings(_env_file=None, app_env="development"),
        engine=engine,
        file_store=LocalFileStore(tmp_path),
        extractor=StubExtractor(),
    )
    with TestClient(app) as client:
        assert client.get("/api/parameters", headers=ANALYST).json()["version"] == 1  # tables and seed exist
    from sqlalchemy import func, select

    from app.db.tables import SupplierRow

    with make_session_factory(engine)() as session:
        assert session.scalar(select(func.count()).select_from(SupplierRow)) == 0


def test_no_endpoint_writes_reference_data(api):
    writers = {"post", "put", "patch", "delete"}
    paths = [
        path
        for path, operations in api.app.openapi()["paths"].items()
        if writers & set(operations)
    ]
    assert sorted(paths) == [
        "/api/invoices",
        "/api/invoices/{invoice_id}/corrections",
        "/api/invoices/{invoice_id}/decisions",
        "/api/invoices/{invoice_id}/retry",
        "/api/parameters",
    ]


def test_unexpected_processing_error_fails_the_invoice_and_it_can_be_retried(api, client, caplog):
    secret = "internal detail that must not leak"

    def broken(file_bytes, filename):
        raise RuntimeError(secret)

    working = api.app.state.container.extractor
    api.app.state.container.extractor = broken
    response = client.post("/api/invoices", files={"file": PDF}, headers=INTAKE)
    assert response.status_code == 202  # the upload itself succeeded
    invoice_id = response.json()["id"]

    seen_by_intake = client.get(f"/api/invoices/{invoice_id}", headers=INTAKE).json()
    assert seen_by_intake["status"] == "EXTRACTION_FAILED"
    assert seen_by_intake["reasons"] == [
        "The document could not be processed because of an unexpected error. It can be retried."
    ]
    events = client.get(f"/api/invoices/{invoice_id}/audit", headers=MANAGER).json()["events"]
    assert events[-1]["event_type"] == "EXTRACTION_FAILED"
    assert events[-1]["details"]["error_code"] == "PROCESSING_ERROR"
    assert events[-1]["details"]["error_type"] == "RuntimeError"
    # The detail is in the server log, and nowhere a user can read it.
    assert secret in caplog.text
    assert secret not in str(seen_by_intake) + str(events)
    assert secret not in str(client.get(f"/api/invoices/{invoice_id}", headers=MANAGER).json())

    api.app.state.container.extractor = working
    api.extractor.queue.append(extraction_result())
    assert client.post(f"/api/invoices/{invoice_id}/retry", headers=ANALYST).status_code == 202
    assert client.get(f"/api/invoices/{invoice_id}", headers=ANALYST).json()["invoice"]["status"] == "AUTO_PAY"
