"""Tests for Gemini provider request timeout behavior."""

import httpx
import pytest

from chat_agent.core.schema import GeminiConfig
from chat_agent.llm.providers.gemini import GeminiClient
from chat_agent.llm.schema import (
    MalformedFunctionCallError,
    Message,
    ToolCall,
    ToolDefinition,
    ToolParameter,
)
from chat_agent.memory import MEMORY_EDIT_DEFINITION


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
    def __init__(self, effects: list, calls: list[dict]):
        self.effects = effects
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, params: dict, headers: dict, json: dict):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "json": json,
            }
        )
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return _FakeResponse(effect)


def _patch_httpx_client(
    monkeypatch,
    effects: list,
    timeouts: list[float] | None = None,
    calls: list[dict] | None = None,
) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.gemini.httpx.Client",
        lambda timeout: _record_timeout(timeout, effects, timeouts, calls),
    )


def _record_timeout(
    timeout: float,
    effects: list,
    timeouts: list[float] | None,
    calls: list[dict] | None,
):
    if timeouts is not None:
        timeouts.append(timeout)
    if calls is None:
        calls = []
    return _FakeHttpxClient(effects, calls)


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


def test_chat_with_memory_edit_tool_includes_array_items_schema(monkeypatch):
    effects = [_text_payload("done")]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls=calls)
    client = _make_client()

    result = client.chat_with_tools(
        [Message(role="user", content="hi")],
        [MEMORY_EDIT_DEFINITION],
    )

    assert result.content == "done"
    declarations = calls[0]["json"]["tools"][0]["functionDeclarations"]
    memory_decl = next(d for d in declarations if d["name"] == "memory_edit")
    requests_schema = memory_decl["parameters"]["properties"]["requests"]
    assert requests_schema["type"] == "array"
    assert requests_schema["items"]["type"] == "object"


def test_chat_with_tools_parses_camel_case_function_call(monkeypatch):
    effects = [
        _multi_part_payload(
            [
                {
                    "functionCall": {
                        "name": "read_file",
                        "args": {"path": "memory/agent/recent.md"},
                    },
                    "thoughtSignature": "sig-123",
                }
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

    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}
    assert result.tool_calls[0].thought_signature == "sig-123"


def test_chat_with_tools_raises_on_malformed_function_call(monkeypatch):
    effects = [
        {
            "candidates": [
                {
                    "content": {},
                    "finishReason": "MALFORMED_FUNCTION_CALL",
                    "finishMessage": "Malformed function call",
                }
            ]
        }
    ]
    _patch_httpx_client(monkeypatch, effects)
    client = _make_client()

    with pytest.raises(MalformedFunctionCallError, match="MALFORMED_FUNCTION_CALL"):
        client.chat_with_tools([Message(role="user", content="hi")], [])


def test_chat_with_tools_serializes_thought_signature_in_history(monkeypatch):
    effects = [_text_payload("done")]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls=calls)
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
    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    name="read_file",
                    arguments={"path": "memory/agent/recent.md"},
                    thought_signature="sig-abc",
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="tc-1",
            content="ok",
        ),
    ]

    result = client.chat_with_tools(messages, tools)

    assert result.content == "done"
    parts = calls[0]["json"]["contents"][1]["parts"]
    function_part = next(p for p in parts if "functionCall" in p)
    assert function_part["thoughtSignature"] == "sig-abc"


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
                {"function_call": {"name": "read_file", "args": {"path": "memory/agent/recent.md"}}},
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
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}


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


def test_chat_includes_generation_config_thinking(monkeypatch):
    effects = [_text_payload("ok")]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls=calls)
    client = _make_client(
        reasoning={"effort": "medium", "max_tokens": 1024},
    )

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    gen_config = calls[0]["json"]["generationConfig"]
    assert gen_config["thinkingConfig"] == {
        "thinkingBudget": 1024,
        "thinkingLevel": "MEDIUM",
    }
    assert gen_config["maxOutputTokens"] == 8192


def test_chat_always_includes_max_output_tokens(monkeypatch):
    effects = [_text_payload("ok")]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls=calls)
    client = _make_client(max_tokens=4096)

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert calls[0]["json"]["generationConfig"]["maxOutputTokens"] == 4096
    assert "thinkingConfig" not in calls[0]["json"]["generationConfig"]
