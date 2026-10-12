"""Service and cache. The model client is a stub; nothing touches the network."""

import json

import pytest

from app.config import Settings
from app.extraction import service
from app.extraction.cache import ExtractionCache, cache_key
from app.extraction.client import ModelCallResult
from app.extraction.errors import RateLimited
from app.extraction.service import ExtractionStatus, extract
from extraction_builders import DEPLOYMENT, FAKE_KEY, make_extraction, make_pdf

PDF = make_pdf(1)


class StubClient:
    deployment = DEPLOYMENT

    def __init__(self, extraction=None, error=None):
        self._extraction = extraction or make_extraction()
        self._error = error
        self.calls = 0

    def extract(self, images):
        self.calls += 1
        if self._error:
            raise self._error
        return ModelCallResult(
            extraction=self._extraction,
            raw_response={"id": "resp_test"},
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            structured_output=True,
            calls=1,
        )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.invalid/openai/v1",
        azure_openai_api_key=FAKE_KEY,
        azure_openai_deployment=DEPLOYMENT,
        extraction_cache_dir=str(tmp_path / "cache"),
    )


def test_invoice_is_extracted_with_metadata(settings):
    client = StubClient()
    result = extract(PDF, "invoice.pdf", settings=settings, client=client)
    assert result.status is ExtractionStatus.EXTRACTED
    assert result.document_type == "invoice"
    assert result.extraction.header.invoice_number.value == "TEST-0001"
    assert result.tokens == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert result.from_cache is False
    assert result.model == DEPLOYMENT
    assert result.prompt_version and result.schema_version
    assert result.errors == []


def test_same_file_does_not_call_the_client_twice(settings):
    client = StubClient()
    first = extract(PDF, "invoice.pdf", settings=settings, client=client)
    second = extract(PDF, "renamed.pdf", settings=settings, client=client)
    assert client.calls == 1
    assert first.from_cache is False and second.from_cache is True
    assert second.tokens is None
    assert second.extraction == first.extraction


def test_cache_entry_stores_raw_and_validated_answer(settings):
    extract(PDF, "invoice.pdf", settings=settings, client=StubClient())
    files = list(settings.extraction_cache_path.glob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["raw_response"] == {"id": "resp_test"}
    assert entry["validated"]["header"]["invoice_number"]["value"] == "TEST-0001"
    assert FAKE_KEY not in files[0].read_text(encoding="utf-8")


def test_changing_prompt_version_invalidates_the_cache(settings, monkeypatch):
    client = StubClient()
    extract(PDF, "invoice.pdf", settings=settings, client=client)
    monkeypatch.setattr(service, "PROMPT_VERSION", "next")
    result = extract(PDF, "invoice.pdf", settings=settings, client=client)
    assert client.calls == 2
    assert result.from_cache is False


def test_changing_schema_version_invalidates_the_cache(settings, monkeypatch):
    client = StubClient()
    extract(PDF, "invoice.pdf", settings=settings, client=client)
    monkeypatch.setattr(service, "SCHEMA_VERSION", "next")
    extract(PDF, "invoice.pdf", settings=settings, client=client)
    assert client.calls == 2


def test_cache_key_depends_on_every_component():
    base = cache_key("file", "p1", "s1", "dep")
    assert cache_key("other", "p1", "s1", "dep") != base
    assert cache_key("file", "p2", "s1", "dep") != base
    assert cache_key("file", "p1", "s2", "dep") != base
    assert cache_key("file", "p1", "s1", "other") != base


def test_no_cache_flag_forces_a_new_call(settings):
    client = StubClient()
    extract(PDF, "invoice.pdf", settings=settings, client=client)
    extract(PDF, "invoice.pdf", settings=settings, client=client, use_cache=False)
    assert client.calls == 2


def test_corrupt_cache_entry_is_a_miss(settings, tmp_path):
    cache = ExtractionCache(tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "abc.json").write_text("{not json", encoding="utf-8")
    assert cache.get("abc") is None


def test_quotation_is_not_an_invoice(settings):
    client = StubClient(make_extraction(document_type="quotation", classification_reason="Titled Quotation."))
    result = extract(PDF, "quote.pdf", settings=settings, client=client)
    assert result.status is ExtractionStatus.NOT_AN_INVOICE
    assert result.document_type == "quotation"
    assert result.classification_reason == "Titled Quotation."


def test_unreadable_document_fails_without_calling_the_model(settings):
    client = StubClient()
    result = extract(b"", "invoice.pdf", settings=settings, client=client)
    assert result.status is ExtractionStatus.EXTRACTION_FAILED
    assert result.error_code == "UNREADABLE_DOCUMENT"
    assert result.errors == ["The file is empty."]
    assert client.calls == 0


def test_model_failure_becomes_extraction_failed_and_is_not_cached(settings):
    failing = StubClient(error=RateLimited("Rate limit reached and still limited after 3 retries."))
    result = extract(PDF, "invoice.pdf", settings=settings, client=failing)
    assert result.status is ExtractionStatus.EXTRACTION_FAILED
    assert result.error_code == "RATE_LIMITED"
    assert not list(settings.extraction_cache_path.glob("*.json"))

    working = StubClient()
    retried = extract(PDF, "invoice.pdf", settings=settings, client=working)
    assert retried.status is ExtractionStatus.EXTRACTED and working.calls == 1


def test_missing_key_fails_cleanly_without_a_request(tmp_path):
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint=None,
        azure_openai_api_key=None,
        extraction_cache_dir=str(tmp_path / "cache"),
    )
    result = extract(PDF, "invoice.pdf", settings=settings)
    assert result.status is ExtractionStatus.EXTRACTION_FAILED
    assert result.error_code == "MISSING_CREDENTIALS"
