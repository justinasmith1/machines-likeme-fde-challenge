"""Extraction entry point: file bytes in, ExtractionResult out. Never raises for a bad
document or a model failure; those become status EXTRACTION_FAILED with a clear error.

Flow: hash -> cache lookup -> render to PNG -> model call -> validation -> cache store.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.extraction.cache import ExtractionCache, cache_key
from app.extraction.client import AzureExtractionClient, ModelCallResult
from app.extraction.errors import ExtractionError
from app.extraction.prompt import PROMPT_VERSION
from app.extraction.render import render_document
from app.extraction.schema import SCHEMA_VERSION, ModelExtraction


class ExtractionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    NOT_AN_INVOICE = "NOT_AN_INVOICE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


@dataclass(frozen=True)
class ExtractionResult:
    status: ExtractionStatus
    document_type: str | None = None
    classification_reason: str | None = None
    extraction: ModelExtraction | None = None
    errors: list[str] = field(default_factory=list)  # plain-language, key-free
    error_code: str | None = None
    tokens: dict[str, int] | None = None  # None when served from cache or on failure
    from_cache: bool = False
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    model: str | None = None  # deployment name
    structured_output: bool | None = None
    file_sha256: str | None = None
    raw_response: dict | None = None  # model response as received; never contains the key


class ExtractionClient(Protocol):
    deployment: str

    def extract(self, images: list[bytes]) -> ModelCallResult: ...


def result_from_extraction(extraction: ModelExtraction, **fields) -> ExtractionResult:
    """Only invoices go on to the rules engine. Anything else is NOT_AN_INVOICE (DL-16)."""
    status = (
        ExtractionStatus.EXTRACTED
        if extraction.document_type == "invoice"
        else ExtractionStatus.NOT_AN_INVOICE
    )
    return ExtractionResult(
        status=status,
        document_type=extraction.document_type,
        classification_reason=extraction.classification_reason,
        extraction=extraction,
        **fields,
    )


def extract(
    file_bytes: bytes,
    filename: str,
    *,
    settings: Settings | None = None,
    client: ExtractionClient | None = None,
    cache: ExtractionCache | None = None,
    use_cache: bool = True,
) -> ExtractionResult:
    settings = settings or get_settings()
    cache = cache or ExtractionCache(settings.extraction_cache_path)
    deployment = client.deployment if client is not None else settings.azure_openai_deployment
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    key = cache_key(file_sha256, PROMPT_VERSION, SCHEMA_VERSION, deployment)
    common = {"model": deployment, "file_sha256": file_sha256}

    if use_cache:
        entry = cache.get(key)
        if entry is not None:
            try:
                extraction = ModelExtraction.model_validate(entry["validated"])
            except (KeyError, ValidationError):
                pass  # unusable entry: fall through and call the model again
            else:
                return result_from_extraction(
                    extraction,
                    from_cache=True,
                    structured_output=entry.get("structured_output"),
                    raw_response=entry.get("raw_response"),
                    **common,
                )

    try:
        images = render_document(
            file_bytes,
            filename,
            dpi=settings.pdf_render_dpi,
            max_pages=settings.pdf_max_pages,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
        if client is None:
            client = AzureExtractionClient.from_settings(settings)
        call = client.extract(images)
    except ExtractionError as exc:
        return ExtractionResult(
            status=ExtractionStatus.EXTRACTION_FAILED,
            errors=[str(exc)],
            error_code=exc.code,
            **common,
        )

    cache.put(
        key,
        {
            "file_sha256": file_sha256,
            "filename": filename,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "deployment": deployment,
            "structured_output": call.structured_output,
            "usage": call.usage,
            "created_at": datetime.now(UTC).isoformat(),
            "raw_response": call.raw_response,
            "validated": call.extraction.model_dump(mode="json"),
        },
    )
    return result_from_extraction(
        call.extraction,
        tokens=call.usage,
        structured_output=call.structured_output,
        raw_response=call.raw_response,
        **common,
    )
