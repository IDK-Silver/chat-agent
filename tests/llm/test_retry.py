"""Tests for generic LLM timeout retry wrapper."""

import httpx
import pytest

from chat_agent.core.schema import OllamaConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.retry import with_timeout_retry
from pydantic import ValidationError

from chat_agent.llm.schema import LLMResponse, MalformedFunctionCallError, Message


class _StubClient:
    def __init__(self, chat_effects: list, tool_effects: list):
        self.chat_effects = chat_effects
        self.tool_effects = tool_effects

    def chat(self, messages: list[Message]) -> str:
        effect = self.chat_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def chat_with_tools(self, messages, tools) -> LLMResponse:
        effect = self.tool_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


def test_retries_chat_timeout():
    base = _StubClient(
        chat_effects=[httpx.TimeoutException("timed out"), "ok"],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_retries_chat_http_502():
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    base = _StubClient(
        chat_effects=[
            httpx.HTTPStatusError(
                "Server error",
                request=request,
                response=httpx.Response(502, request=request),
            ),
            "ok",
        ],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_retries_chat_http_500():
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    base = _StubClient(
        chat_effects=[
            httpx.HTTPStatusError(
                "Server error",
                request=request,
                response=httpx.Response(500, request=request),
            ),
            "ok",
        ],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_retries_chat_with_tools_timeout():
    base = _StubClient(
        chat_effects=[],
        tool_effects=[
            httpx.TimeoutException("timed out"),
            LLMResponse(content="done", tool_calls=[]),
        ],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.content == "done"


def test_raises_after_retry_exhausted():
    base = _StubClient(
        chat_effects=[
            httpx.TimeoutException("timed out"),
            httpx.TimeoutException("timed out again"),
        ],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    with pytest.raises(httpx.TimeoutException):
        client.chat([Message(role="user", content="hi")])


def test_does_not_retry_non_transient_http_error():
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    base = _StubClient(
        chat_effects=[
            httpx.HTTPStatusError(
                "Unauthorized",
                request=request,
                response=httpx.Response(401, request=request),
            )
        ],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=2)

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([Message(role="user", content="hi")])


def test_retries_malformed_function_call():
    base = _StubClient(
        chat_effects=[],
        tool_effects=[
            MalformedFunctionCallError("malformed"),
            LLMResponse(content="ok", tool_calls=[]),
        ],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.content == "ok"


def test_retries_validation_error():
    """Pydantic ValidationError from malformed API response is retryable."""
    from pydantic import BaseModel

    class _Dummy(BaseModel):
        choices: list[str]

    def _raise_validation():
        _Dummy.model_validate({})  # missing 'choices'

    try:
        _raise_validation()
    except ValidationError as e:
        first_error = e
    else:
        pytest.fail("expected ValidationError")

    base = _StubClient(
        chat_effects=[first_error, "recovered"],
        tool_effects=[],
    )
    client = with_timeout_retry(base, timeout_retries=1)

    result = client.chat([Message(role="user", content="hi")])
    assert result == "recovered"


def test_no_wrapper_when_retry_zero():
    base = _StubClient(chat_effects=["ok"], tool_effects=[])
    client = with_timeout_retry(base, timeout_retries=0)
    assert client is base


def test_create_client_applies_request_timeout_override(monkeypatch):
    observed_timeouts: list[float] = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "ok", "thinking": None}}

    class _FakeHttpxClient:
        def __init__(self, timeout: float):
            observed_timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict):
            return _FakeResponse()

    monkeypatch.setattr(
        "chat_agent.llm.providers.ollama.httpx.Client",
        _FakeHttpxClient,
    )

    cfg = OllamaConfig(provider="ollama", model="test-model", base_url="http://localhost:11434")
    client = create_client(cfg, request_timeout=7.0)
    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert observed_timeouts == [7.0]
