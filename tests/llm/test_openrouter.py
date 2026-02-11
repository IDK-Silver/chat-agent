"""Tests for OpenRouter provider reasoning payload mapping."""

from chat_agent.core.schema import OpenRouterConfig, ReasoningConfig
from chat_agent.llm.providers.openrouter import OpenRouterClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, make_openai_payload


def _patch_httpx_client(monkeypatch, payload: dict, calls: list[dict]) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([payload], calls),
    )


def test_chat_includes_openrouter_reasoning_object(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
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
    _patch_httpx_client(monkeypatch, make_openai_payload("done"), calls)
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
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
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
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
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
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
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
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert "reasoning" not in calls[0]["json"]


def test_chat_includes_site_headers(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            site_url="https://chat-agent.local",
            site_name="chat-agent",
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["headers"]["HTTP-Referer"] == "https://chat-agent.local"
    assert calls[0]["headers"]["X-Title"] == "chat-agent"
