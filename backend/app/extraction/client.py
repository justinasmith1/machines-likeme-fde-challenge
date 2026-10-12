"""Azure OpenAI client for invoice extraction (Responses API, v1).

POST {AZURE_OPENAI_ENDPOINT}/responses with header "api-key". The "model" field is the
deployment name.

Output mode: structured outputs (text.format json_schema, strict) are requested first.
If the deployment rejects that parameter with a 400, the client falls back once to asking
for JSON in the prompt. Either way the answer is validated with Pydantic. The mode that
was used is reported in ModelCallResult.structured_output.

Failure handling (DL-41):
  - 429: wait for Retry-After if present, otherwise exponential backoff with jitter.
    After max_retries retries: RateLimited.
  - 5xx, timeouts, connection errors: backoff and retry. Then ModelUnavailable.
  - Other 4xx (400, 401, 403...): no retry, ModelRequestRejected.
  - Output that is not valid against the schema: one retry, then InvalidModelOutput.
  - Truncated output (status incomplete): TruncatedModelOutput, no retry.

The API key is only ever placed in the request header. It is never logged and is masked
out of any error text that comes back from the service.
"""

import base64
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.extraction.errors import (
    InvalidModelOutput,
    MissingCredentials,
    ModelRequestRejected,
    ModelUnavailable,
    RateLimited,
    TruncatedModelOutput,
)
from app.extraction.prompt import build_prompt
from app.extraction.schema import EXTRACTION_JSON_SCHEMA, SCHEMA_NAME, ModelExtraction

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 3000
BACKOFF_BASE_SECONDS = 2.0
MAX_WAIT_SECONDS = 120.0
INVALID_OUTPUT_RETRIES = 1


@dataclass(frozen=True)
class ModelCallResult:
    extraction: ModelExtraction
    raw_response: dict[str, Any]
    usage: dict[str, int] | None  # tokens of the last call
    structured_output: bool  # True: json_schema mode. False: JSON-in-prompt fallback.
    calls: int  # HTTP requests made, including retries


class _StructuredOutputNotSupported(Exception):
    pass


def require_credentials(settings: Settings) -> None:
    """Raise MissingCredentials before any network call if the endpoint or key is not set."""
    missing = []
    if not settings.azure_openai_endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if settings.azure_openai_api_key is None or not settings.azure_openai_api_key.get_secret_value():
        missing.append("AZURE_OPENAI_API_KEY")
    if missing:
        raise MissingCredentials(f"{' and '.join(missing)} not set. Add to .env; see .env.example.")


class AzureExtractionClient:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: SecretStr,
        deployment: str,
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        structured_output: bool = True,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/responses"
        self._api_key = api_key
        self.deployment = deployment
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._http = http or httpx.Client()
        self._sleep = sleep
        self._rng = rng
        self._structured_output = structured_output

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> "AzureExtractionClient":
        require_credentials(settings)
        return cls(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            deployment=settings.azure_openai_deployment,
            timeout_seconds=settings.extraction_timeout_seconds,
            max_retries=settings.extraction_max_retries,
            **kwargs,
        )

    def __repr__(self) -> str:  # never expose the key
        return f"AzureExtractionClient(deployment={self.deployment!r})"

    # ---- public API

    def extract(self, images: list[bytes]) -> ModelCallResult:
        """Send the page images and return the validated extraction."""
        calls = 0
        invalid_retries_left = INVALID_OUTPUT_RETRIES
        while True:
            try:
                data, made = self._post_with_retries(self._body(images))
            except _StructuredOutputNotSupported:
                calls += 1
                logger.warning(
                    "Deployment rejected structured outputs; falling back to JSON in the prompt"
                )
                self._structured_output = False
                continue
            calls += made
            usage = self._usage(data)
            if usage:
                logger.info(
                    "Extraction call tokens: input=%s output=%s total=%s",
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                )
            try:
                extraction = self._parse(data)
            except InvalidModelOutput as exc:
                if invalid_retries_left > 0:
                    invalid_retries_left -= 1
                    logger.warning("Model output did not validate; retrying once: %s", exc)
                    continue
                raise
            return ModelCallResult(
                extraction=extraction,
                raw_response=data,
                usage=usage,
                structured_output=self._structured_output,
                calls=calls,
            )

    # ---- request

    def _body(self, images: list[bytes]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": build_prompt(include_schema=not self._structured_output),
            }
        ]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}
            )
        body: dict[str, Any] = {
            "model": self.deployment,
            "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if self._structured_output:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "strict": True,
                    "schema": EXTRACTION_JSON_SCHEMA,
                }
            }
        return body

    def _post_with_retries(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            is_last = attempt == self._max_retries
            try:
                response = self._http.post(
                    self._url,
                    headers={"api-key": self._api_key.get_secret_value()},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException:
                last_error = ModelUnavailable(
                    f"The model did not answer within {self._timeout:g} seconds."
                )
                wait = self._backoff(attempt)
            except httpx.TransportError as exc:
                last_error = ModelUnavailable(
                    f"Could not reach the model service ({type(exc).__name__})."
                )
                wait = self._backoff(attempt)
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return response.json(), attempt + 1
                    except ValueError as exc:
                        raise InvalidModelOutput("The service returned a non-JSON body.") from exc
                detail = self._error_detail(response)
                if status == 429:
                    last_error = RateLimited(
                        f"Rate limit reached and still limited after {self._max_retries} retries."
                    )
                    wait = self._retry_after(response)
                    if wait is None:
                        wait = self._backoff(attempt)
                elif status >= 500:
                    last_error = ModelUnavailable(f"The model service returned HTTP {status}.")
                    wait = self._backoff(attempt)
                else:
                    if status == 400 and self._structured_output and _is_format_error(response):
                        raise _StructuredOutputNotSupported
                    raise ModelRequestRejected(
                        status, f"The model service rejected the request (HTTP {status}): {detail}"
                    )
            if not is_last:
                logger.warning(
                    "Extraction attempt %s failed (%s); waiting %.1fs before retrying",
                    attempt + 1,
                    type(last_error).__name__,
                    wait,
                )
                self._sleep(wait)
        assert last_error is not None
        raise last_error

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter: 2s, 4s, 8s... plus up to 1s."""
        return min(BACKOFF_BASE_SECONDS * (2**attempt) + self._rng(), MAX_WAIT_SECONDS)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        return min(max(seconds, 0.0), MAX_WAIT_SECONDS)

    def _error_detail(self, response: httpx.Response) -> str:
        """Short, key-free description of an error response."""
        message = ""
        try:
            error = response.json().get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
        except (ValueError, AttributeError):
            pass
        return self._mask(message)[:300] or "no detail given"

    def _mask(self, text: str) -> str:
        key = self._api_key.get_secret_value()
        return text.replace(key, "***") if key else text

    # ---- response

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, int] | None:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        return {
            name: usage[name]
            for name in ("input_tokens", "output_tokens", "total_tokens")
            if isinstance(usage.get(name), int)
        }

    @staticmethod
    def _parse(data: dict[str, Any]) -> ModelExtraction:
        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise TruncatedModelOutput(f"The model response was cut off ({reason}).")

        texts: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise InvalidModelOutput("The model refused to process the document.")
                if part.get("type") == "output_text":
                    texts.append(part.get("text") or "")
        text = "".join(texts).strip()
        if not text:
            raise InvalidModelOutput("The model returned no text.")
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise InvalidModelOutput("The model output is not valid JSON.") from exc
        try:
            return ModelExtraction.model_validate(payload)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            )
            raise InvalidModelOutput(
                f"The model output does not match the schema ({problems})."
            ) from exc


def _is_format_error(response: httpx.Response) -> bool:
    """True when a 400 is about the structured output parameter itself."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return False
    param = str(error.get("param") or "")
    message = str(error.get("message") or "").lower()
    return (
        param.startswith("text")
        or "json_schema" in message
        or "text.format" in message
        or "response_format" in message
    )
