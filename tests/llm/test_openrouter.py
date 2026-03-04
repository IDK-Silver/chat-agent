"""Tests for OpenRouter provider reasoning payload mapping."""

from pathlib import Path

import pytest

from chat_agent.core.schema import (
    OpenRouterConfig,
    OpenRouterProviderRoutingConfig,
    OpenRouterReasoningConfig,
)
from chat_agent.llm.providers.openai_compat import OpenAICompatibleClient
from chat_agent.llm.providers.openrouter import OpenRouterClient
from chat_agent.llm.schema import ContentPart, Message, ToolDefinition, ToolParameter

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
            reasoning=OpenRouterReasoningConfig(
                effort="high",
                supported_efforts=["low", "medium", "high"],
            ),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning"] == {"effort": "high"}


def test_chat_reasoning_disabled_sends_effort_none(monkeypatch):
    """enabled=false should send effort=none to OpenRouter."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            reasoning=OpenRouterReasoningConfig(enabled=False),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["reasoning"] == {"effort": "none"}


def test_chat_reasoning_max_tokens_only(monkeypatch):
    """max_tokens without effort should use max_tokens."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-flash-preview",
            api_key="test-key",
            reasoning=OpenRouterReasoningConfig(max_tokens=4096),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["reasoning"] == {"max_tokens": 4096}


def test_config_rejects_effort_and_max_tokens_together():
    """effort and max_tokens are mutually exclusive at config level."""
    config = OpenRouterConfig(
        provider="openrouter",
        model="google/gemini-3-flash-preview",
        api_key="test-key",
        reasoning=OpenRouterReasoningConfig(
            effort="medium",
            max_tokens=2048,
            supported_efforts=["low", "medium", "high"],
        ),
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        config.validate_reasoning(source_path=Path("test.yaml"))


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
    assert calls[0]["headers"]["X-OpenRouter-Title"] == "chat-agent"
    assert calls[0]["headers"]["X-Title"] == "chat-agent"


def test_chat_with_tools_sends_reasoning(monkeypatch):
    """Reasoning should be included in tool-calling requests."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("done"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-pro-preview",
            api_key="test-key",
            reasoning=OpenRouterReasoningConfig(
                enabled=False,
            ),
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

    assert calls[0]["json"]["reasoning"] == {"effort": "none"}
    assert "tools" in calls[0]["json"]


def test_chat_includes_provider_routing_payload(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            api_key="test-key",
            provider_routing=OpenRouterProviderRoutingConfig(
                order=["google-vertex"],
                allow_fallbacks=False,
            ),
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["provider"] == {
        "order": ["google-vertex"],
        "allow_fallbacks": False,
    }


def test_chat_omits_provider_when_provider_routing_is_none(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            api_key="test-key",
            provider_routing=None,
        )
    )

    client.chat([Message(role="user", content="hello")])

    assert "provider" not in calls[0]["json"]


# --- cache_control passthrough ---


def test_convert_content_parts_passes_through_cache_control():
    """cache_control on ContentPart should appear in the converted dict."""
    parts = [
        ContentPart(
            type="text",
            text="hello",
            cache_control={"type": "ephemeral", "ttl": "1h"},
        )
    ]
    result = OpenAICompatibleClient._convert_content_parts(parts)
    assert result[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_convert_content_parts_omits_cache_control_when_none():
    """No cache_control means the field is omitted from the dict."""
    parts = [ContentPart(type="text", text="hello")]
    result = OpenAICompatibleClient._convert_content_parts(parts)
    assert "cache_control" not in result[0]


def test_system_message_with_cache_control_sent_as_content_array(monkeypatch):
    """System message with ContentPart list preserves cache_control in payload."""
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            api_key="test-key",
        )
    )

    messages = [
        Message(role="system", content=[
            ContentPart(
                type="text",
                text="You are helpful.",
                cache_control={"type": "ephemeral", "ttl": "1h"},
            ),
        ]),
        Message(role="user", content="hello"),
    ]
    client.chat(messages)

    sent_messages = calls[0]["json"]["messages"]
    sys_msg = sent_messages[0]
    assert sys_msg["role"] == "system"
    assert isinstance(sys_msg["content"], list)
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_chat_with_tools_parses_usage_and_cache_tokens(monkeypatch):
    calls: list[dict] = []
    payload = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 2100,
            "completion_tokens": 77,
            "total_tokens": 2177,
            "prompt_tokens_details": {
                "cached_tokens": 2048,
                "cache_write_tokens": 32,
            },
        },
    }
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.6",
            api_key="test-key",
        )
    )

    result = client.chat_with_tools([Message(role="user", content="hello")], [])

    assert result.usage_available is True
    assert result.prompt_tokens == 2100
    assert result.completion_tokens == 77
    assert result.total_tokens == 2177
    assert result.cache_read_tokens == 2048
    assert result.cache_write_tokens == 32
