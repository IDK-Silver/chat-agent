"""Tests for the native Codex provider client."""

import pytest

from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import CodexConfig, CodexReasoningConfig
from chat_agent.llm.providers.codex import CodexClient
from chat_agent.llm.schema import Message, ToolDefinition, ToolParameter

from .conftest import FakeHttpxClient


def _patch_httpx_client(
    monkeypatch,
    effects: dict | list[dict],
    calls: list[dict],
) -> None:
    shared_effects = effects if isinstance(effects, list) else [effects]
    monkeypatch.setattr(
        "chat_agent.llm.providers.codex.httpx.Client",
        lambda timeout: FakeHttpxClient(shared_effects, calls),
    )


def test_chat_returns_content(monkeypatch):
    payload = {"content": "hello from codex"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CodexClient(CodexConfig(model="gpt-5.2-codex"))

    result = client.chat([Message(role="user", content="hi")])

    assert result == "hello from codex"
    assert calls[0]["url"] == "http://localhost:4143/chat"


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
    client = CodexClient(CodexConfig(model="gpt-5.2-codex"))
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
    assert calls[0]["json"]["tools"][0]["name"] == "read_file"


def test_reasoning_effort_passed(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CodexClient(
        CodexConfig(
            model="gpt-5.2-codex",
            reasoning=CodexReasoningConfig(effort="medium"),
        )
    )

    client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["reasoning_effort"] == "medium"


def test_chat_passes_response_schema(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CodexClient(CodexConfig(model="gpt-5.2-codex"))

    client.chat(
        [Message(role="user", content="hi")],
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )

    assert calls[0]["json"]["response_schema"]["type"] == "object"


def test_chat_passes_prompt_cache_key(monkeypatch):
    payload = {"content": "ok"}
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = CodexClient(
        CodexConfig(model="gpt-5.4"),
        cache_key_provider=lambda: "session-1:brain:20260411",
    )

    client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["prompt_cache_key"] == "session-1:brain:20260411"


def test_codex_config_default_base_url():
    config = CodexConfig(model="test")
    assert config.base_url == "http://localhost:4143"


def test_codex_config_rejects_openai_compat_base_url():
    with pytest.raises(ValueError, match="proxy root"):
        CodexConfig(model="test", base_url="http://localhost:4143/v1")


@pytest.mark.parametrize(
    ("path", "model", "reasoning_enabled"),
    [
        ("llm/codex/gpt-5.4/no-thinking.yaml", "gpt-5.4", False),
        ("llm/codex/gpt-5.4/thinking.yaml", "gpt-5.4", True),
        ("llm/codex/gpt-5.4-mini/no-thinking.yaml", "gpt-5.4-mini", False),
        ("llm/codex/gpt-5.4-mini/thinking.yaml", "gpt-5.4-mini", True),
        ("llm/codex/gpt-5.3-codex/no-thinking.yaml", "gpt-5.3-codex", False),
        ("llm/codex/gpt-5.3-codex/thinking.yaml", "gpt-5.3-codex", True),
        ("llm/codex/gpt-5.3-codex-spark/no-thinking.yaml", "gpt-5.3-codex-spark", False),
        ("llm/codex/gpt-5.3-codex-spark/thinking.yaml", "gpt-5.3-codex-spark", True),
        ("llm/codex/gpt-5.2/no-thinking.yaml", "gpt-5.2", False),
        ("llm/codex/gpt-5.2/thinking.yaml", "gpt-5.2", True),
    ],
)
def test_repo_codex_profiles_load(path: str, model: str, reasoning_enabled: bool):
    config = resolve_llm_config(path)

    assert isinstance(config, CodexConfig)
    assert config.model == model
    assert config.reasoning is not None
    assert config.reasoning.enabled is reasoning_enabled
    assert config.reasoning.supported_efforts == ["low", "medium", "high"]
