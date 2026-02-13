"""Tests for Copilot provider behavior (OpenAI-compatible, no auth)."""

from chat_agent.core.schema import CopilotConfig, ReasoningConfig
from chat_agent.llm.providers.copilot import CopilotClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient, make_openai_payload


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
    assert result.tool_calls[0].arguments == {"path": "memory/agent/short-term.md"}


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
            reasoning=ReasoningConfig(effort="medium"),
        )
    )

    client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["reasoning_effort"] == "medium"


def test_copilot_config_default_base_url():
    config = CopilotConfig(model="test")
    assert config.base_url == "http://localhost:4141/v1"
