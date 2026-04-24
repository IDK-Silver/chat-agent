"""Tests for OpenAI provider reasoning payload mapping."""

from chat_agent.core.schema import OpenAIConfig, OpenAIReasoningConfig
from chat_agent.llm.providers.openai import OpenAIClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, make_openai_payload


def _patch_httpx_client(monkeypatch, payload: dict, calls: list[dict]) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([payload], calls),
    )


def test_chat_includes_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
            reasoning=OpenAIReasoningConfig(effort="high"),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning_effort"] == "high"


def test_chat_includes_max_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-5.1",
            api_key="test-key",
            reasoning=OpenAIReasoningConfig(effort="max"),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning_effort"] == "max"


def test_chat_with_tools_uses_override_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("done"), calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
            reasoning=OpenAIReasoningConfig(enabled=False),
            provider_overrides={"openai_reasoning_effort": "low"},
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

    assert calls[0]["json"]["reasoning_effort"] == "low"
    assert "tools" in calls[0]["json"]


def test_chat_with_tools_parses_usage_tokens(monkeypatch):
    calls: list[dict] = []
    payload = {
        "choices": [{"message": {"content": "done"}}],
        "usage": {
            "prompt_tokens": 1234,
            "completion_tokens": 56,
            "total_tokens": 1290,
            "prompt_tokens_details": {
                "cached_tokens": 1200,
                "cache_write_tokens": 12,
            },
        },
    }
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
        )
    )

    result = client.chat_with_tools([Message(role="user", content="hello")], [])

    assert result.usage_available is True
    assert result.prompt_tokens == 1234
    assert result.completion_tokens == 56
    assert result.total_tokens == 1290
    assert result.cache_read_tokens == 1200
    assert result.cache_write_tokens == 12
