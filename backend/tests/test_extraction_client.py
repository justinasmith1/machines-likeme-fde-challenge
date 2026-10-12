"""Azure client behaviour. Every request goes to httpx.MockTransport; sleep is injected."""

import json
import logging

import httpx
import pytest

from app.config import Settings
from app.extraction.client import AzureExtractionClient, require_credentials
from app.extraction.errors import (
    InvalidModelOutput,
    MissingCredentials,
    ModelRequestRejected,
    ModelUnavailable,
    RateLimited,
    TruncatedModelOutput,
)
from extraction_builders import (
    DEPLOYMENT,
    ENDPOINT,
    FAKE_KEY,
    api_response,
    make_client,
    make_payload,
    make_png,
    sequence,
)

IMAGES = [make_png()]
OK = httpx.Response(200, json=api_response(make_payload()))


def test_successful_call_sends_the_expected_request():
    client, sleeps, requests = make_client(sequence(OK))
    result = client.extract(IMAGES)

    assert result.extraction.header.invoice_number.value == "TEST-0001"
    assert result.usage == {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}
    assert result.structured_output is True
    assert result.calls == 1 and sleeps == []

    request = requests[0]
    assert str(request.url) == f"{ENDPOINT}/responses"
    assert request.headers["api-key"] == FAKE_KEY
    body = json.loads(request.content)
    assert body["model"] == DEPLOYMENT
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}
    assert body["max_output_tokens"] == 3000
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    content = body["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_429_with_retry_after_waits_that_long_and_retries():
    limited = httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "7"})
    client, sleeps, requests = make_client(sequence(limited, OK))
    result = client.extract(IMAGES)
    assert sleeps == [7.0]
    assert len(requests) == 2
    assert result.calls == 2


def test_429_without_retry_after_uses_exponential_backoff_with_jitter():
    limited = httpx.Response(429, json={"error": {"message": "slow down"}})
    client, sleeps, _ = make_client(sequence(limited, limited, OK))
    client.extract(IMAGES)
    assert sleeps == [2.5, 4.5]  # 2s and 4s, plus the injected 0.5 jitter


def test_429_until_retries_run_out_raises_rate_limited():
    limited = httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "1"})
    client, sleeps, requests = make_client(sequence(limited), max_retries=3)
    with pytest.raises(RateLimited):
        client.extract(IMAGES)
    assert len(requests) == 4  # first attempt plus 3 retries
    assert sleeps == [1.0, 1.0, 1.0]


def test_500_is_retried():
    client, sleeps, requests = make_client(sequence(httpx.Response(500, text="boom"), OK))
    client.extract(IMAGES)
    assert len(requests) == 2 and len(sleeps) == 1


def test_500_until_retries_run_out_raises_model_unavailable():
    client, _, requests = make_client(sequence(httpx.Response(503, text="down")), max_retries=2)
    with pytest.raises(ModelUnavailable, match="HTTP 503"):
        client.extract(IMAGES)
    assert len(requests) == 3


def test_timeout_is_retried_then_raises_model_unavailable():
    client, sleeps, requests = make_client(sequence(httpx.ReadTimeout("timed out")), max_retries=1)
    with pytest.raises(ModelUnavailable, match="did not answer"):
        client.extract(IMAGES)
    assert len(requests) == 2 and len(sleeps) == 1


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_are_not_retried(status):
    rejected = httpx.Response(status, json={"error": {"message": "not allowed"}})
    client, sleeps, requests = make_client(sequence(rejected))
    with pytest.raises(ModelRequestRejected) as exc:
        client.extract(IMAGES)
    assert exc.value.status_code == status
    assert len(requests) == 1 and sleeps == []


def test_invalid_json_is_retried_once_then_raises():
    bad = httpx.Response(200, json=api_response(None, text="this is not json"))
    client, _, requests = make_client(sequence(bad))
    with pytest.raises(InvalidModelOutput, match="not valid JSON"):
        client.extract(IMAGES)
    assert len(requests) == 2  # original plus exactly one retry


def test_invalid_json_then_valid_answer_succeeds():
    bad = httpx.Response(200, json=api_response(None, text="{broken"))
    client, _, requests = make_client(sequence(bad, OK))
    result = client.extract(IMAGES)
    assert result.calls == 2 and len(requests) == 2


def test_output_not_matching_schema_raises_invalid_model_output():
    payload = make_payload()
    del payload["header"]["currency"]
    bad = httpx.Response(200, json=api_response(payload))
    client, _, _ = make_client(sequence(bad))
    with pytest.raises(InvalidModelOutput, match="does not match the schema"):
        client.extract(IMAGES)


def test_truncated_response_raises_and_is_not_retried():
    body = api_response(make_payload(), status="incomplete")
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    client, _, requests = make_client(sequence(httpx.Response(200, json=body)))
    with pytest.raises(TruncatedModelOutput, match="max_output_tokens"):
        client.extract(IMAGES)
    assert len(requests) == 1


def test_falls_back_to_json_in_prompt_when_structured_outputs_are_rejected():
    unsupported = httpx.Response(
        400, json={"error": {"message": "Unsupported parameter: 'text.format'", "param": "text.format"}}
    )
    client, _, requests = make_client(sequence(unsupported, OK))
    result = client.extract(IMAGES)

    assert result.structured_output is False
    first, second = (json.loads(r.content) for r in requests)
    assert "text" in first and "text" not in second
    assert "JSON schema" in second["input"][0]["content"][0]["text"]


def test_token_usage_is_logged(caplog):
    client, _, _ = make_client(sequence(OK))
    with caplog.at_level(logging.INFO, logger="app.extraction.client"):
        client.extract(IMAGES)
    assert "input=1200 output=300 total=1500" in caplog.text


def test_api_key_never_appears_in_errors_or_logs(caplog):
    """Every failure path, including a service that echoes the key back."""
    echo = f"Invalid key {FAKE_KEY} supplied"
    scenarios = [
        sequence(httpx.Response(401, json={"error": {"message": echo}})),
        sequence(httpx.Response(400, json={"error": {"message": echo}})),
        sequence(httpx.Response(429, json={"error": {"message": echo}})),
        sequence(httpx.Response(500, text=echo)),
        sequence(httpx.ConnectError(echo)),
        sequence(httpx.Response(200, json=api_response(None, text=echo))),
    ]
    messages = []
    with caplog.at_level(logging.DEBUG):
        for handler in scenarios:
            client, _, _ = make_client(handler, max_retries=1)
            with pytest.raises(Exception) as exc:
                client.extract(IMAGES)
            messages.append(str(exc.value))
            messages.append(repr(exc.value))
            messages.append(repr(client))
    assert len(messages) == 18
    assert all(FAKE_KEY not in message for message in messages)
    assert FAKE_KEY not in caplog.text


def test_missing_credentials_fail_before_any_request():
    settings = Settings(_env_file=None, azure_openai_endpoint=None, azure_openai_api_key=None)
    with pytest.raises(MissingCredentials, match="AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY"):
        require_credentials(settings)
    with pytest.raises(MissingCredentials):
        AzureExtractionClient.from_settings(settings)


def test_empty_key_counts_as_missing():
    settings = Settings(_env_file=None, azure_openai_endpoint=ENDPOINT, azure_openai_api_key="")
    with pytest.raises(MissingCredentials, match="AZURE_OPENAI_API_KEY"):
        require_credentials(settings)


def test_unmocked_network_call_fails():
    """The autouse guard in conftest blocks real connections."""
    with pytest.raises(AssertionError, match="Real network call attempted"):
        httpx.Client().get("https://example.invalid/")
