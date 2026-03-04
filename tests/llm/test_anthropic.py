"""Tests for Anthropic provider text-block parsing behavior."""

from __future__ import annotations

from chat_agent.core.schema import AnthropicConfig
from chat_agent.llm.providers.anthropic import AnthropicClient
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
        "chat_agent.llm.providers.anthropic.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload, calls),
    )


def _make_client() -> AnthropicClient:
    config = AnthropicConfig(
        provider="anthropic",
        model="claude-sonnet-test",
        api_key="test-key",
    )
    return AnthropicClient(config)


def test_chat_concatenates_multiple_text_blocks(monkeypatch):
    payload = {
        "content": [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = _make_client()

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello world"


def test_chat_with_tools_concatenates_text_and_parses_tool_calls(monkeypatch):
    payload = {
        "content": [
            {"type": "text", "text": "prefix "},
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "read_file",
                "input": {"path": "memory/agent/recent.md"},
            },
            {"type": "text", "text": "suffix"},
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = _make_client()

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
    result = client.chat_with_tools([Message(role="user", content="hi")], tools)

    assert result.content == "prefix suffix"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "tool-1"
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}


def test_chat_includes_thinking_payload_when_enabled(monkeypatch):
    payload = {
        "content": [
            {"type": "text", "text": "ok"},
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    config = AnthropicConfig(
        provider="anthropic",
        model="claude-sonnet-test",
        api_key="test-key",
        reasoning={"enabled": True, "max_tokens": 1024},
    )
    client = AnthropicClient(config)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert calls[0]["json"]["thinking"] == {"type": "enabled", "budget_tokens": 1024}


def test_chat_with_tools_parses_usage_tokens(monkeypatch):
    payload = {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": 3000,
            "output_tokens": 120,
            "cache_read_input_tokens": 2500,
            "cache_creation_input_tokens": 64,
        },
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = _make_client()

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.usage_available is True
    assert result.prompt_tokens == 5564
    assert result.completion_tokens == 120
    assert result.total_tokens == 5684
    assert result.cache_read_tokens == 2500
    assert result.cache_write_tokens == 64
