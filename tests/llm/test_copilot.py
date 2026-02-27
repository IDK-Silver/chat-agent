"""Tests for Copilot provider behavior (OpenAI-compatible, no auth)."""

import pytest

from chat_agent.core.schema import CopilotConfig, CopilotReasoningConfig
from chat_agent.llm.providers.copilot import CopilotClient
from chat_agent.llm.schema import ContextLengthExceededError, Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, FakeResponse, make_openai_payload


def _patch_httpx_client(
    monkeypatch,
    effects: dict | list[dict],
    calls: list[dict],
) -> None:
    shared_effects: list[dict]
    if isinstance(effects, list):
        shared_effects = effects
    else:
        shared_effects = [effects]
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient(shared_effects, calls),
    )


def test_chat_returns_content(monkeypatch):
    payload = make_openai_payload("hello from copilot")
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="claude-sonnet-4"))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello from copilot"
    assert calls[0]["url"] == "http://localhost:4141/v1/chat/completions"


def test_chat_reads_content_from_later_choice(monkeypatch):
    payload = {
        "choices": [
            {"message": {"content": None}},
            {"message": {"content": "hello from choice 1"}},
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="claude-sonnet-4"))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello from choice 1"


def test_chat_with_tools_parses_tool_calls(monkeypatch):
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
                                "arguments": '{"path": "memory/agent/recent.md"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gpt-4.1"))

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
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}


def test_chat_with_tools_parses_reasoning_content(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": "thinking block",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "memory/agent/recent.md"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gemini-3-pro-preview"))

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

    assert result.reasoning_content == "thinking block"


def test_chat_with_tools_parses_reasoning_text_alias(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_text": "alias thinking block",
                    "tool_calls": [],
                }
            }
        ]
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gemini-3.1-pro-preview"))

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.reasoning_content == "alias thinking block"


def test_no_auth_header_sent(monkeypatch):
    payload = make_openai_payload("ok")
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    client.chat([Message(role="user", content="hi")])

    assert "Authorization" not in calls[0]["headers"]


def test_reasoning_effort_passed(monkeypatch):
    payload = make_openai_payload("ok")
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
    assert "reasoning" not in calls[0]["json"]


def test_reasoning_disabled_omits_reasoning_fields(monkeypatch):
    payload = make_openai_payload("ok")
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CopilotClient(
        CopilotConfig(
            model="gpt-4.1",
            reasoning=CopilotReasoningConfig(enabled=False),
        )
    )

    client.chat([Message(role="user", content="hi")])

    assert "reasoning" not in calls[0]["json"]
    assert "reasoning_effort" not in calls[0]["json"]


def test_copilot_config_default_base_url():
    config = CopilotConfig(model="test")
    assert config.base_url == "http://localhost:4141/v1"


# ---- Token limit detection ----


def test_token_limit_raises_context_length_exceeded(monkeypatch):
    """HTTP 400 with max_prompt_tokens_exceeded raises ContextLengthExceededError."""
    error_body = {
        "error": {
            "message": '{"error":{"message":"prompt token count of 120008 '
            'exceeds the limit of 64000",'
            '"code":"model_max_prompt_tokens_exceeded"}}',
            "type": "error",
        }
    }
    error_response = FakeResponse(error_body, status_code=400)
    calls: list[dict] = []
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([error_response], calls),
    )
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    with pytest.raises(ContextLengthExceededError, match="max_prompt_tokens_exceeded"):
        client.chat([Message(role="user", content="hi")])


def test_context_length_exceeded_code_raises(monkeypatch):
    """HTTP 400 with context_length_exceeded code also raises."""
    error_body = {
        "error": {
            "message": "context_length_exceeded",
            "type": "invalid_request_error",
        }
    }
    error_response = FakeResponse(error_body, status_code=400)
    calls: list[dict] = []
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([error_response], calls),
    )
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    with pytest.raises(ContextLengthExceededError):
        client.chat([Message(role="user", content="hi")])


def test_non_token_limit_400_raises_http_error(monkeypatch):
    """HTTP 400 without token limit keywords raises normal HTTPStatusError."""
    import httpx

    error_body = {"error": {"message": "invalid model", "type": "error"}}
    error_response = FakeResponse(error_body, status_code=400)
    calls: list[dict] = []
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([error_response], calls),
    )
    client = CopilotClient(CopilotConfig(model="gpt-4o"))

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([Message(role="user", content="hi")])
