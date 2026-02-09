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
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, headers: dict, json: dict) -> _FakeResponse:
        return _FakeResponse(self.payload)


def _patch_httpx_client(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.anthropic.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload),
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
    _patch_httpx_client(monkeypatch, payload)
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
                "input": {"path": "memory/short-term.md"},
            },
            {"type": "text", "text": "suffix"},
        ]
    }
    _patch_httpx_client(monkeypatch, payload)
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
    assert result.tool_calls[0].arguments == {"path": "memory/short-term.md"}
