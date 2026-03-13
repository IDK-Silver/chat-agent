"""Tests for the native Copilot provider client."""

import pytest

from chat_agent.core.schema import CopilotConfig, CopilotReasoningConfig
from chat_agent.llm.providers.copilot import CopilotClient
from chat_agent.llm.schema import ContextLengthExceededError, Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, FakeResponse


def _patch_httpx_client(
    monkeypatch,
    effects: dict | list[dict],
    calls: list[dict],
) -> None:
    shared_effects = effects if isinstance(effects, list) else [effects]
    monkeypatch.setattr(
        "chat_agent.llm.providers.copilot.httpx.Client",
        lambda timeout: FakeHttpxClient(shared_effects, calls),
    )


def test_chat_returns_content(monkeypatch):
    payload = {"content": "hello from copilot"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="claude-sonnet-4"))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello from copilot"
    assert calls[0]["url"] == "http://localhost:4141/chat"
    assert calls[0]["json"]["initiator"] == "user"


def test_chat_with_tools_returns_tool_calls(monkeypatch):
    payload = {
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "name": "read_file",
                "arguments": {"path": "memory/agent/recent.md"},
            }
        ],
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gpt-4.1"))

    tools = [
        ToolDefinition(
            name="read_file",
            description="read file",
            parameters={"path": ToolParameter(type="string", description="path")},
            required=["path"],
        )
    ]
    result = client.chat_with_tools([Message(role="user", content="hi")], tools)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}


def test_chat_with_tools_returns_reasoning_content(monkeypatch):
    payload = {
        "content": "",
        "reasoning_content": "thinking block",
        "tool_calls": [],
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gemini-3-pro-preview"))

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.reasoning_content == "thinking block"


def test_reasoning_effort_passed(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(
        CopilotConfig(
            model="gpt-5.1",
            reasoning=CopilotReasoningConfig(effort="medium"),
        )
    )

    client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["reasoning_effort"] == "medium"


def test_chat_with_tools_passes_tool_definition(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gpt-4o"))
    tools = [
        ToolDefinition(
            name="read_file",
            description="read file",
            parameters={"path": ToolParameter(type="string", description="path")},
            required=["path"],
        )
    ]

    client.chat_with_tools([Message(role="user", content="hi")], tools)

    assert calls[0]["json"]["tools"][0]["name"] == "read_file"


def test_chat_passes_response_schema(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    client.chat(
        [Message(role="user", content="hi")],
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    assert calls[0]["json"]["response_schema"]["type"] == "object"


def test_copilot_config_default_base_url():
    config = CopilotConfig(model="test")
    assert config.base_url == "http://localhost:4141"


def test_copilot_config_rejects_openai_compat_base_url():
    with pytest.raises(ValueError, match="proxy root"):
        CopilotConfig(model="test", base_url="http://localhost:4141/v1")


def test_token_limit_raises_context_length_exceeded(monkeypatch):
    error_body = {
        "error": {
            "message": '{"error":{"message":"prompt token count of 120008 exceeds the limit of 64000","code":"model_max_prompt_tokens_exceeded"}}',
            "type": "error",
        }
    }
    error_response = FakeResponse(error_body, status_code=400)
    calls: list[dict] = []
    monkeypatch.setattr(
        "chat_agent.llm.providers.copilot.httpx.Client",
        lambda timeout: FakeHttpxClient([error_response], calls),
    )
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    with pytest.raises(ContextLengthExceededError, match="max_prompt_tokens_exceeded"):
        client.chat([Message(role="user", content="hi")])


def test_non_token_limit_400_raises_http_error(monkeypatch):
    import httpx

    error_body = {"error": {"message": "invalid model", "type": "error"}}
    error_response = FakeResponse(error_body, status_code=400)
    calls: list[dict] = []
    monkeypatch.setattr(
        "chat_agent.llm.providers.copilot.httpx.Client",
        lambda timeout: FakeHttpxClient([error_response], calls),
    )
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([Message(role="user", content="hi")])
