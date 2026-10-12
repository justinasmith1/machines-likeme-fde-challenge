"""Typed extraction errors. Messages are safe to show and log: they never contain the API key."""


class ExtractionError(Exception):
    """Base class. `code` is a stable identifier stored with the invoice."""

    code = "EXTRACTION_ERROR"


class UnreadableDocument(ExtractionError):
    """The uploaded file cannot be turned into images: empty, corrupt, too large, too long."""

    code = "UNREADABLE_DOCUMENT"


class UnsupportedDocumentType(UnreadableDocument):
    code = "UNSUPPORTED_DOCUMENT_TYPE"


class MissingCredentials(ExtractionError):
    code = "MISSING_CREDENTIALS"


class RateLimited(ExtractionError):
    """HTTP 429 after all retries. The invoice can be retried later."""

    code = "RATE_LIMITED"


class ModelUnavailable(ExtractionError):
    """5xx, timeouts or connection errors after all retries."""

    code = "MODEL_UNAVAILABLE"


class ModelRequestRejected(ExtractionError):
    """4xx that a retry cannot fix (bad request, authentication, permission)."""

    code = "MODEL_REQUEST_REJECTED"

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class InvalidModelOutput(ExtractionError):
    """The model answered, but not with JSON that validates against the schema."""

    code = "INVALID_MODEL_OUTPUT"


class TruncatedModelOutput(ExtractionError):
    """The response was cut off (status incomplete / max_output_tokens)."""

    code = "TRUNCATED_MODEL_OUTPUT"
