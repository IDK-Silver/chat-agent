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
