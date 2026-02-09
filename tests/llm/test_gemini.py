"""Tests for Gemini provider request timeout behavior."""

import httpx
import pytest

from chat_agent.core.schema import GeminiConfig
from chat_agent.llm.providers.gemini import GeminiClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter


def _text_payload(text: str) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": text}],
                }
            }
        ]
    }


def _multi_part_payload(parts: list[dict]) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": parts,
                }
            }
        ]
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _FakeHttpxClient:
    def __init__(self, effects: list):
        self.effects = effects

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, params: dict, headers: dict, json: dict):
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return _FakeResponse(effect)


def _patch_httpx_client(monkeypatch, effects: list, timeouts: list[float] | None = None) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.gemini.httpx.Client",
        lambda timeout: _record_timeout(timeout, effects, timeouts),
    )


def _record_timeout(timeout: float, effects: list, timeouts: list[float] | None):
    if timeouts is not None:
        timeouts.append(timeout)
    return _FakeHttpxClient(effects)


def _make_client(**overrides) -> GeminiClient:
    config = GeminiConfig(
        provider="gemini",
        model="gemini-2.5-flash",
        api_key="test-key",
        **overrides,
    )
    return GeminiClient(config)


def test_chat_returns_text(monkeypatch):
    effects = [_text_payload("ok")]
    _patch_httpx_client(monkeypatch, effects)
    client = _make_client()

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"


def test_chat_with_tools_returns_text(monkeypatch):
    effects = [_text_payload("done")]
    _patch_httpx_client(monkeypatch, effects)
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

    assert result.content == "done"


def test_chat_concatenates_multiple_text_parts(monkeypatch):
    effects = [_multi_part_payload([{"text": "hello "}, {"text": "world"}])]
    _patch_httpx_client(monkeypatch, effects)
    client = _make_client()

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello world"


def test_chat_with_tools_concatenates_text_parts_around_tool_call(monkeypatch):
    effects = [
        _multi_part_payload(
            [
                {"text": "prefix "},
                {"function_call": {"name": "read_file", "args": {"path": "memory/short-term.md"}}},
                {"text": "suffix"},
            ]
        )
    ]
    _patch_httpx_client(monkeypatch, effects)
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
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/short-term.md"}


def test_chat_raises_timeout(monkeypatch):
    effects = [httpx.TimeoutException("timed out")]
    _patch_httpx_client(monkeypatch, effects)
    client = _make_client()

    with pytest.raises(httpx.TimeoutException):
        client.chat([Message(role="user", content="hi")])


def test_chat_uses_configurable_timeout(monkeypatch):
    effects = [_text_payload("ok")]
    observed_timeouts: list[float] = []
    _patch_httpx_client(monkeypatch, effects, observed_timeouts)
    client = _make_client(request_timeout=7.5)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert observed_timeouts == [7.5]
