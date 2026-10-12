"""Builders for extraction tests. All data is fictional."""

import copy
import json

import httpx
import pymupdf
from pydantic import SecretStr

from app.extraction.client import AzureExtractionClient
from app.extraction.schema import ModelExtraction

FAKE_KEY = "test-key-0123456789abcdef0123456789abcdef"
ENDPOINT = "https://example.invalid/openai/v1"
DEPLOYMENT = "test-deployment"


def read(value, suggested=None) -> dict:
    return {"value": value, "status": "read", "suggested_value": suggested}


def not_read(status: str, suggested=None) -> dict:
    return {"value": None, "status": status, "suggested_value": suggested}


def make_payload(**overrides) -> dict:
    """A model answer for a clean one-line invoice. Override top-level keys, or use
    header={...} / lines=[...] to replace parts."""
    payload = {
        "document_type": "invoice",
        "classification_reason": "Requests payment for delivered goods.",
        "header": {
            "supplier_name": "Test Supplier B",
            "supplier_tax_id": "TAX-B",
            "invoice_number": read("TEST-0001"),
            "invoice_date": read("2026-02-15"),
            "due_date": "2026-03-15",
            "po_references": {"values": ["PO-2026-0002"], "status": "read", "suggested_values": []},
            "currency": "EUR",
            "subtotal": "100.00",
            "tax_amount": "21.00",
            "total_amount": "121.00",
        },
        "lines": [
            {
                "description": "Synthetic test item",
                "quantity": read("4"),
                "unit_price": read("25.00"),
                "line_total": "100.00",
                "po_line_reference": 1,
            }
        ],
    }
    header = overrides.pop("header", None)
    payload = copy.deepcopy(payload)
    if header:
        payload["header"].update(header)
    payload.update(overrides)
    return payload


def make_extraction(**overrides) -> ModelExtraction:
    return ModelExtraction.model_validate(make_payload(**overrides))


def api_response(payload, *, status="completed", usage=True, text=None) -> dict:
    """A Responses API body whose output text is the given payload."""
    body = {
        "id": "resp_test",
        "status": status,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": text if text is not None else json.dumps(payload)}
                ],
            },
        ],
    }
    if usage:
        body["usage"] = {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}
    return body


def make_client(handler, *, max_retries=3, structured_output=True):
    """Client wired to an httpx.MockTransport. Returns (client, sleeps, requests)."""
    sleeps: list[float] = []
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = AzureExtractionClient(
        endpoint=ENDPOINT,
        api_key=SecretStr(FAKE_KEY),
        deployment=DEPLOYMENT,
        timeout_seconds=5,
        max_retries=max_retries,
        http=httpx.Client(transport=httpx.MockTransport(recording_handler)),
        sleep=sleeps.append,
        rng=lambda: 0.5,
        structured_output=structured_output,
    )
    return client, sleeps, requests


def sequence(*responses):
    """Handler that returns the given responses in order (the last one repeats)."""
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def make_pdf(pages: int = 1) -> bytes:
    document = pymupdf.open()
    for number in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Synthetic test page {number + 1}")
    data = document.tobytes()
    document.close()
    return data


def make_png(width: int = 40, height: int = 30) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(200)
    return pixmap.tobytes("png")


def make_jpg() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pixmap.clear_with(120)
    return pixmap.tobytes("jpeg")
