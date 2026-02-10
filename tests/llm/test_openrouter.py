"""Tests for OpenRouter provider reasoning payload mapping."""

from chat_agent.core.schema import OpenRouterConfig, ReasoningConfig
from chat_agent.llm.providers.openrouter import OpenRouterClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _FakeHttpxClient:
    def __init__(self, payload: dict, calls: list[dict]):
        self.payload = payload
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, headers: dict, json: dict) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self.payload)


def _patch_httpx_client(monkeypatch, payload: dict, calls: list[dict]) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.openrouter.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload, calls),
    )


def _make_payload(content: str = "ok") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_chat_includes_openrouter_reasoning_object(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-pro-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(effort="high"),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning"] == {"effort": "high"}


def test_chat_with_tools_uses_openrouter_reasoning_override(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("done"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-pro-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(enabled=False),
            provider_overrides={"openrouter_reasoning": {"enabled": False}},
        )
    )

    tools = [
        ToolDefinition(
            name="read_file",
            description="read file",
            parameters={
                "path": ToolParameter(type="string", description="path"),
            },
            required=["path"],
        )
    ]
    _ = client.chat_with_tools([Message(role="user", content="hello")], tools)

    assert calls[0]["json"]["reasoning"] == {"enabled": False}
    assert "tools" in calls[0]["json"]


def test_chat_reasoning_disabled_omits_field(monkeypatch):
    """enabled=false without override should omit reasoning from request."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(enabled=False),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert "reasoning" not in calls[0]["json"]


def test_chat_reasoning_max_tokens_only(monkeypatch):
    """max_tokens without effort should use max_tokens."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(max_tokens=4096),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["reasoning"] == {"max_tokens": 4096}


def test_chat_reasoning_effort_takes_precedence_over_max_tokens(monkeypatch):
    """When both effort and max_tokens are set, only effort is sent."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(effort="medium", max_tokens=2048),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["reasoning"] == {"effort": "medium"}


def test_chat_no_reasoning_config_omits_field(monkeypatch):
    """No reasoning config should omit reasoning from request."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert "reasoning" not in calls[0]["json"]
