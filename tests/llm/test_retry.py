"""Tests for generic LLM retry wrapper."""

import logging

import httpx
import pytest

from chat_agent.core.schema import OllamaNativeConfig, OllamaNativeToggleThinkingConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.http_error import (
    classify_http_status_error,
    format_http_status_error,
)
from chat_agent.llm.retry import (
    _429_BACKOFF_SCHEDULE,
    _429_sleep_seconds,
    _TRANSIENT_BACKOFF_SCHEDULE,
    _retry_reason,
    _transient_sleep_seconds,
    with_llm_retry,
)
from pydantic import ValidationError

from chat_agent.llm.schema import LLMResponse, MalformedFunctionCallError, Message


def _make_429(*, headers=None):
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.HTTPStatusError(
        "Rate limited",
        request=request,
        response=httpx.Response(429, request=request, headers=headers or {}),
    )


def _make_status(code):
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.HTTPStatusError(
        f"HTTP {code}",
        request=request,
        response=httpx.Response(code, request=request),
    )


def _make_status_with_text(code, text):
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.HTTPStatusError(
        f"HTTP {code}",
        request=request,
        response=httpx.Response(code, request=request, text=text),
    )


class _StubClient:
    def __init__(self, chat_effects: list, tool_effects: list):
        self.chat_effects = chat_effects
        self.tool_effects = tool_effects

    def chat(self, messages: list[Message], response_schema=None, temperature=None) -> str:
        effect = self.chat_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def chat_with_tools(self, messages, tools, temperature=None) -> LLMResponse:
        effect = self.tool_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


# ---- Existing transient/retryable tests ----


def test_retries_chat_timeout():
    base = _StubClient(
        chat_effects=[httpx.TimeoutException("timed out"), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_retries_chat_http_502():
    base = _StubClient(
        chat_effects=[_make_status(502), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_retries_chat_http_500():
    base = _StubClient(
        chat_effects=[_make_status(500), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_transient_retry_backoff_schedule(monkeypatch):
    base = _StubClient(
        chat_effects=[httpx.TimeoutException("timed out"), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [_TRANSIENT_BACKOFF_SCHEDULE[0]]


def test_retries_chat_with_tools_timeout():
    base = _StubClient(
        chat_effects=[],
        tool_effects=[
            httpx.TimeoutException("timed out"),
            LLMResponse(content="done", tool_calls=[]),
        ],
    )
    client = with_llm_retry(base, transient_retries=1)

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
    client = with_llm_retry(base, transient_retries=1)

    with pytest.raises(httpx.TimeoutException):
        client.chat([Message(role="user", content="hi")])


def test_does_not_retry_non_transient_http_error():
    base = _StubClient(
        chat_effects=[_make_status(401)],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=2, rate_limit_retries=2)

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([Message(role="user", content="hi")])


def test_classify_http_400_request_format():
    err = _make_status_with_text(
        400,
        '{"error":"Function call is missing a thought_signature in functionCall parts."}',
    )

    assert classify_http_status_error(err) == "request-format"
    assert _retry_reason(err) == "http 400 (request-format)"
    assert (
        format_http_status_error(err)
        == "HTTP 400 (request-format): Function call is missing a thought_signature in functionCall parts."
    )


def test_classify_http_400_provider_api():
    err = _make_status_with_text(
        400,
        '{"error":"Model does not support this feature."}',
    )

    assert classify_http_status_error(err) == "provider-api"
    assert _retry_reason(err) == "http 400 (provider-api)"
    assert (
        format_http_status_error(err)
        == "HTTP 400 (provider-api): Model does not support this feature."
    )


def test_retries_malformed_function_call():
    base = _StubClient(
        chat_effects=[],
        tool_effects=[
            MalformedFunctionCallError("malformed"),
            LLMResponse(content="ok", tool_calls=[]),
        ],
    )
    client = with_llm_retry(base, transient_retries=1)

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
    client = with_llm_retry(base, transient_retries=1)

    result = client.chat([Message(role="user", content="hi")])
    assert result == "recovered"


def test_no_wrapper_when_retry_zero():
    base = _StubClient(chat_effects=["ok"], tool_effects=[])
    client = with_llm_retry(base, transient_retries=0)
    assert client is base


def test_no_wrapper_when_both_zero():
    base = _StubClient(chat_effects=["ok"], tool_effects=[])
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=0)
    assert client is base


def test_wrapper_created_when_only_rate_limit():
    base = _StubClient(chat_effects=["ok"], tool_effects=[])
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    assert client is not base


def test_create_client_applies_request_timeout_override(monkeypatch):
    observed_timeouts: list[float] = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"role": "assistant", "content": "ok"},
                "done_reason": "stop",
            }

    class _FakeHttpxClient:
        def __init__(self, timeout: float):
            observed_timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, headers: dict, json: dict):
            return _FakeResponse()

    monkeypatch.setattr(
        "chat_agent.llm.providers.ollama_native.httpx.Client",
        _FakeHttpxClient,
    )

    cfg = OllamaNativeConfig(
        provider="ollama",
        model="test-model",
        base_url="http://localhost:11434",
        thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
    )
    client = create_client(cfg, request_timeout=7.0)
    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert observed_timeouts == [7.0]


# ---- 429 independent retry tests ----


def test_429_uses_rate_limit_retries_not_timeout(monkeypatch):
    """429 errors use rate_limit_retries counter, not transient_retries."""
    base = _StubClient(
        chat_effects=[_make_429(), "ok"],
        tool_effects=[],
    )
    # transient_retries=0 but rate_limit_retries=1 -> should still retry 429
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [5.0]  # first schedule entry


def test_429_does_not_consume_transient_retries(monkeypatch):
    """A 429 retry doesn't reduce the transient retry budget."""
    base = _StubClient(
        chat_effects=[
            _make_429(),
            httpx.TimeoutException("timed out"),
            "ok",
        ],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1, rate_limit_retries=1)
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_timeout_does_not_consume_rate_limit_retries(monkeypatch):
    """A timeout retry doesn't reduce the 429 retry budget."""
    base = _StubClient(
        chat_effects=[
            httpx.TimeoutException("timed out"),
            _make_429(),
            "ok",
        ],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1, rate_limit_retries=1)
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_429_backoff_schedule(monkeypatch):
    """429 retries follow the fixed backoff schedule."""
    errors = [_make_429() for _ in range(5)]
    base = _StubClient(
        chat_effects=[*errors, "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=5)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [5.0, 10.0, 20.0, 30.0, 30.0]


def test_429_retry_after_takes_max_with_schedule(monkeypatch):
    """When Retry-After header is present, use max(header, schedule)."""
    # Retry-After: 50 > schedule[0]=5.0 -> use 50
    base = _StubClient(
        chat_effects=[_make_429(headers={"Retry-After": "50"}), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [50.0]


def test_429_retry_after_smaller_than_schedule_uses_schedule(monkeypatch):
    """When Retry-After < schedule, use the schedule value."""
    # Retry-After: 1.0 < schedule[0]=5.0 -> use 5.0
    base = _StubClient(
        chat_effects=[_make_429(headers={"Retry-After": "1.0"}), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [5.0]


def test_429_exhaustion_raises(monkeypatch):
    """When 429 retries are exhausted, the exception is raised."""
    base = _StubClient(
        chat_effects=[_make_429(), _make_429()],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.chat([Message(role="user", content="hi")])
    assert exc_info.value.response.status_code == 429


def test_429_no_retries_when_rate_limit_zero():
    """429 is raised immediately when rate_limit_retries=0."""
    base = _StubClient(
        chat_effects=[_make_429()],
        tool_effects=[],
    )
    # Only transient_retries, no rate_limit_retries -> 429 not retried
    client = with_llm_retry(base, transient_retries=3, rate_limit_retries=0)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.chat([Message(role="user", content="hi")])
    assert exc_info.value.response.status_code == 429


def test_429_debug_log_output(monkeypatch, caplog):
    """429 retries emit debug log messages."""
    base = _StubClient(
        chat_effects=[_make_429(), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    with caplog.at_level(logging.DEBUG, logger="chat_agent.llm.retry"):
        client.chat([Message(role="user", content="hi")])

    assert any("429 retry 1/1" in record.message for record in caplog.records)


def test_transient_debug_log_output_for_503(monkeypatch, caplog):
    base = _StubClient(
        chat_effects=[_make_status(503), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1, rate_limit_retries=0)
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    with caplog.at_level(logging.DEBUG, logger="chat_agent.llm.retry"):
        client.chat([Message(role="user", content="hi")])

    assert any("transient retry 1/1" in record.message for record in caplog.records)
    assert any("http 503" in record.message for record in caplog.records)


def test_transient_debug_log_includes_label(monkeypatch, caplog):
    base = _StubClient(
        chat_effects=[httpx.TimeoutException("timed out"), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=1, label="memory_editor")
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: None)

    with caplog.at_level(logging.DEBUG, logger="chat_agent.llm.retry"):
        client.chat([Message(role="user", content="hi")])

    assert any("[memory_editor] transient retry 1/1" in record.message for record in caplog.records)


def test_429_sleep_seconds_helper():
    """_429_sleep_seconds returns schedule values for each attempt."""
    exc = _make_429()
    for i, expected in enumerate(_429_BACKOFF_SCHEDULE):
        assert _429_sleep_seconds(exc, i) == expected

    # Beyond schedule length clamps to last value
    assert _429_sleep_seconds(exc, len(_429_BACKOFF_SCHEDULE)) == _429_BACKOFF_SCHEDULE[-1]
    assert _429_sleep_seconds(exc, 100) == _429_BACKOFF_SCHEDULE[-1]


def test_transient_sleep_seconds_helper_uses_schedule():
    exc = httpx.TimeoutException("timed out")
    assert _transient_sleep_seconds(exc, 0) == _TRANSIENT_BACKOFF_SCHEDULE[0]


def test_transient_sleep_seconds_helper_uses_bounded_jitter(monkeypatch):
    exc = httpx.TimeoutException("timed out")
    calls: list[tuple[float, float]] = []

    def _fake_uniform(low: float, high: float) -> float:
        calls.append((low, high))
        return 0.75

    monkeypatch.setattr("chat_agent.llm.retry.random.uniform", _fake_uniform)

    delay = _transient_sleep_seconds(exc, 1)

    assert delay == 0.75
    assert calls == [(_TRANSIENT_BACKOFF_SCHEDULE[0], _TRANSIENT_BACKOFF_SCHEDULE[1])]


def test_transient_sleep_seconds_helper_clamps_after_schedule(monkeypatch):
    exc = httpx.TimeoutException("timed out")
    calls: list[tuple[float, float]] = []

    def _fake_uniform(low: float, high: float) -> float:
        calls.append((low, high))
        return high

    monkeypatch.setattr("chat_agent.llm.retry.random.uniform", _fake_uniform)

    delay = _transient_sleep_seconds(exc, len(_TRANSIENT_BACKOFF_SCHEDULE) + 3)

    # idx is clamped to last bucket; jitter between last two buckets
    last = _TRANSIENT_BACKOFF_SCHEDULE[-1]
    prev = _TRANSIENT_BACKOFF_SCHEDULE[-2]
    if last > prev:
        assert delay == last  # _fake_uniform returns high
        assert calls == [(prev, last)]
    else:
        assert delay == last
        assert calls == []


# ---- Updated existing 429 tests (now use rate_limit_retries) ----


def test_retries_chat_http_429_waits_retry_after_seconds(monkeypatch):
    """Retry-After header larger than schedule -> use Retry-After."""
    base = _StubClient(
        chat_effects=[_make_429(headers={"Retry-After": "15"}), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [15.0]  # max(15, schedule[0]=5.0)


def test_retries_chat_http_429_waits_schedule_without_header(monkeypatch):
    """No Retry-After header -> use schedule backoff."""
    base = _StubClient(
        chat_effects=[_make_429(), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [5.0]  # schedule[0]


def test_retries_chat_http_429_retry_after_zero_uses_schedule(monkeypatch):
    """Retry-After: 0 -> max(0, schedule[0]) = schedule value."""
    base = _StubClient(
        chat_effects=[_make_429(headers={"Retry-After": "0"}), "ok"],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=0, rate_limit_retries=1)
    sleeps: list[float] = []
    monkeypatch.setattr("chat_agent.llm.retry.time.sleep", lambda secs: sleeps.append(secs))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert sleeps == [5.0]  # max(0, schedule[0]=5.0)


# ---- ContextLengthExceededError tests ----


def test_context_length_exceeded_not_retried():
    """ContextLengthExceededError is not retryable and propagates immediately."""
    from chat_agent.llm.schema import ContextLengthExceededError

    base = _StubClient(
        chat_effects=[ContextLengthExceededError("token limit exceeded")],
        tool_effects=[],
    )
    client = with_llm_retry(base, transient_retries=3, rate_limit_retries=3)

    with pytest.raises(ContextLengthExceededError):
        client.chat([Message(role="user", content="hi")])


def test_context_length_exceeded_not_retried_chat_with_tools():
    """ContextLengthExceededError propagates immediately from chat_with_tools."""
    from chat_agent.llm.schema import ContextLengthExceededError

    base = _StubClient(
        chat_effects=[],
        tool_effects=[ContextLengthExceededError("token limit exceeded")],
    )
    client = with_llm_retry(base, transient_retries=3, rate_limit_retries=3)

    with pytest.raises(ContextLengthExceededError):
        client.chat_with_tools([Message(role="user", content="hi")], [])
