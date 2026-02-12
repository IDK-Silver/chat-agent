"""Tests for Ollama provider behavior (OpenAI-compatible)."""

import httpx
import pytest

from chat_agent.core.schema import OllamaConfig, ReasoningConfig
from chat_agent.llm.providers.ollama import OllamaClient
from chat_agent.llm.schema import Message, ToolCall, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, FakeResponse, make_openai_payload


def _patch_httpx_client(
    monkeypatch,
    effects: dict | Exception | list[dict | Exception],
    calls: list[dict],
) -> None:
    shared_effects: list[dict | Exception]
    if isinstance(effects, list):
        shared_effects = effects
    else:
        shared_effects = [effects]
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient(shared_effects, calls),
    )


def test_chat_returns_content_when_present(monkeypatch):
    payload = make_openai_payload('{"passed": true, "violations": [], "guidance": ""}')
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaClient(
        OllamaConfig(provider="ollama", model="kimi-k2.5:cloud")
    )

    result = client.chat([Message(role="user", content="hi")])

    assert result == '{"passed": true, "violations": [], "guidance": ""}'
    assert calls[0]["url"].endswith("/chat/completions")


def test_chat_falls_back_to_thinking_when_content_empty(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "thinking": '```json\n{"passed": true}\n```',
                }
            }
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaClient(
        OllamaConfig(provider="ollama", model="kimi-k2.5:cloud")
    )

    result = client.chat([Message(role="user", content="hi")])

    assert "passed" in result


def test_chat_with_tools_uses_openai_compat_and_parses_tool_calls(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "memory/agent/short-term.md"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaClient(
        OllamaConfig(provider="ollama", model="kimi-k2.5:cloud")
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
    result = client.chat_with_tools([Message(role="user", content="hi")], tools)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/agent/short-term.md"}
    assert calls[0]["url"].endswith("/chat/completions")
    assert "tools" in calls[0]["json"]


def test_chat_with_tools_maps_reasoning_to_effort(monkeypatch):
    payload = make_openai_payload("ok")
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaClient(
        OllamaConfig(
            provider="ollama",
            model="gpt-oss:20b",
            reasoning=ReasoningConfig(effort="medium"),
        )
    )

    _ = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert calls[0]["json"]["reasoning_effort"] == "medium"


def test_chat_with_tools_raises_on_500_with_tool_history(monkeypatch):
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    server_500 = httpx.HTTPStatusError(
        "Server error",
        request=request,
        response=httpx.Response(500, request=request),
    )
    effects: list[dict | Exception] = [server_500]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls)
    client = OllamaClient(
        OllamaConfig(provider="ollama", model="kimi-k2.5:cloud")
    )

    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="tc1",
                    name="read_file",
                    arguments={"path": "memory/agent/short-term.md"},
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="tc1",
            content="recent context",
        ),
    ]
    with pytest.raises(httpx.HTTPStatusError):
        client.chat_with_tools(messages, [])

    assert len(calls) == 1
    assert any(m["role"] == "tool" for m in calls[0]["json"]["messages"])
