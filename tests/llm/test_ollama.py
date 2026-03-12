"""Tests for Ollama native provider behavior."""

import httpx
import pytest

from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import (
    OllamaNativeConfig,
    OllamaNativeEffortThinkingConfig,
    OllamaNativeToggleThinkingConfig,
)
from chat_agent.llm.providers.ollama_native import OllamaNativeClient
from chat_agent.llm.schema import (
    ContentPart,
    ContextLengthExceededError,
    MalformedFunctionCallError,
    Message,
    ToolCall,
    ToolDefinition,
    ToolParameter,
)

from .conftest import FakeHttpxClient, FakeResponse


def _patch_httpx_client(
    monkeypatch,
    effects: dict | FakeResponse | Exception | list[dict | FakeResponse | Exception],
    calls: list[dict],
) -> None:
    shared_effects: list[dict | FakeResponse | Exception]
    if isinstance(effects, list):
        shared_effects = effects
    else:
        shared_effects = [effects]
    monkeypatch.setattr(
        "chat_agent.llm.providers.ollama_native.httpx.Client",
        lambda timeout: FakeHttpxClient(shared_effects, calls),
    )


def test_chat_returns_content_and_uses_native_endpoint(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
        "prompt_eval_count": 9,
        "eval_count": 4,
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert calls[0]["url"].endswith("/api/chat")
    assert calls[0]["json"]["think"] is True
    assert calls[0]["json"]["stream"] is False




def test_chat_sends_bearer_auth_when_api_key_is_configured(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gpt-oss:20b-cloud",
            api_key="test-ollama-key",
            thinking=OllamaNativeEffortThinkingConfig(mode="effort", effort="low"),
        )
    )

    result = client.chat([Message(role="user", content="hi")])

    assert result == "ok"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-ollama-key"

def test_chat_with_tools_parses_tool_calls_and_usage(monkeypatch):
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": "need to read memory",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "memory/agent/recent.md"},
                    }
                }
            ],
        },
        "done_reason": "tool_call",
        "prompt_eval_count": 20,
        "eval_count": 6,
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
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
    result = client.chat_with_tools([Message(role="user", content="hi")], tools)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "ollama-tool-1"
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "memory/agent/recent.md"}
    assert result.reasoning_content == "need to read memory"
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 6
    assert result.total_tokens == 26
    assert result.usage_available is True
    assert "tools" in calls[0]["json"]


def test_chat_with_tools_preserves_provider_tool_call_metadata(monkeypatch):
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "provider-call-1",
                    "thoughtSignature": "sig-123",
                    "providerExtra": "keep-me",
                    "function": {
                        "index": 7,
                        "name": "read_file",
                        "arguments": {"path": "memory/agent/recent.md"},
                    }
                }
            ],
        },
        "done_reason": "tool_call",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "provider-call-1"
    assert result.tool_calls[0].provider_call_index == 7
    assert result.tool_calls[0].thought_signature == "sig-123"
    assert result.tool_calls[0].provider_roundtrip == {
        "id": "provider-call-1",
        "thoughtSignature": "sig-123",
        "providerExtra": "keep-me",
        "function": {
            "index": 7,
            "name": "read_file",
            "arguments": {"path": "memory/agent/recent.md"},
        },
    }


def test_chat_with_tools_raises_on_empty_tool_name(monkeypatch):
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "   ",
                        "arguments": {},
                    }
                }
            ],
        },
        "done_reason": "tool_call",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    with pytest.raises(MalformedFunctionCallError, match="MALFORMED_FUNCTION_CALL"):
        client.chat_with_tools([Message(role="user", content="hi")], [])


def test_chat_maps_effort_mode_for_gpt_oss(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gpt-oss:20b-cloud",
            thinking=OllamaNativeEffortThinkingConfig(mode="effort", effort="medium"),
            vision=False,
        )
    )

    _ = client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["think"] == "medium"


def test_chat_maps_temperature_and_num_predict(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="glm-5:cloud",
            max_tokens=2048,
            temperature=0.2,
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=False),
            vision=False,
        )
    )

    _ = client.chat([Message(role="user", content="hi")])

    assert calls[0]["json"]["options"] == {"num_predict": 2048, "temperature": 0.2}
    assert calls[0]["json"]["think"] is False


def test_chat_serializes_tool_images_as_follow_up_user_message(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(
            role="tool",
            name="capture_screen",
            tool_call_id="tool-1",
            content=[
                ContentPart(type="text", text="screen"),
                ContentPart(type="image", media_type="image/jpeg", data="abc123"),
            ],
        ),
    ]

    _ = client.chat(messages)

    assert calls[0]["json"]["messages"] == [
        {"role": "tool", "content": "screen", "tool_name": "capture_screen"},
        {"role": "user", "images": ["abc123"]},
    ]


def test_chat_with_tools_repairs_missing_tool_results(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
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
                    arguments={"path": "memory/agent/recent.md"},
                )
            ],
        ),
    ]

    _ = client.chat_with_tools(messages, [])

    assert calls[0]["json"]["messages"][-1] == {
        "role": "tool",
        "content": "[Recovered missing tool result]",
        "tool_name": "read_file",
    }


def test_chat_with_tools_repairs_missing_tool_name_from_tool_call_id(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="tc1",
                    name="send_message",
                    arguments={"text": "ping"},
                )
            ],
        ),
        Message(
            role="tool",
            tool_call_id="tc1",
            content="OK: sent to discord",
        ),
    ]

    _ = client.chat_with_tools(messages, [])

    assert calls[0]["json"]["messages"][-1] == {
        "role": "tool",
        "content": "OK: sent to discord",
        "tool_name": "send_message",
    }


def test_chat_with_tools_round_trips_provider_tool_call_metadata(monkeypatch):
    effects = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "provider-call-1",
                        "thoughtSignature": "sig-abc",
                        "providerExtra": "keep-me",
                        "function": {
                            "index": 3,
                            "name": "read_file",
                            "arguments": {"path": "memory/agent/recent.md"},
                        }
                    }
                ],
            },
            "done_reason": "tool_call",
        },
        {
            "message": {"role": "assistant", "content": "done"},
            "done_reason": "stop",
        },
    ]
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, effects, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
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

    first = client.chat_with_tools([Message(role="user", content="hi")], tools)
    second_messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=first.tool_calls),
        Message(
            role="tool",
            name="read_file",
            tool_call_id=first.tool_calls[0].id,
            content="recent context",
        ),
    ]

    result = client.chat_with_tools(second_messages, tools)

    assert result.content == "done"
    assert calls[1]["json"]["messages"][1]["tool_calls"] == [
        {
            "id": "provider-call-1",
            "thoughtSignature": "sig-abc",
            "providerExtra": "keep-me",
            "function": {
                "name": "read_file",
                "arguments": {"path": "memory/agent/recent.md"},
                "index": 3,
            },
        }
    ]


def test_chat_with_tools_textifies_synthetic_tool_history(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "done"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="boot_ctx_0_0",
                    name="read_startup_context",
                    arguments={"file": "memory/agent/index.md"},
                ),
                ToolCall(
                    id="boot_ctx_0_1",
                    name="read_startup_context",
                    arguments={"file": "memory/agent/temp-memory.md"},
                ),
            ],
        ),
        Message(
            role="tool",
            name="read_startup_context",
            tool_call_id="boot_ctx_0_0",
            content='<file path="memory/agent/index.md">\nindex\n</file>',
        ),
        Message(
            role="tool",
            name="read_startup_context",
            tool_call_id="boot_ctx_0_1",
            content='<file path="memory/agent/temp-memory.md">\ntemp\n</file>',
        ),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="stage1_deadbeef",
                    name="_stage1_gather",
                    arguments={},
                )
            ],
        ),
        Message(
            role="tool",
            name="_stage1_gather",
            tool_call_id="stage1_deadbeef",
            content="[stage1 findings]",
        ),
    ]

    result = client.chat_with_tools(messages, [])

    assert result.content == "done"
    assert calls[0]["json"]["messages"] == [
        {
            "role": "system",
            "content": '[Synthetic context: read_startup_context]\n<file path="memory/agent/index.md">\nindex\n</file>',
        },
        {
            "role": "system",
            "content": '[Synthetic context: read_startup_context]\n<file path="memory/agent/temp-memory.md">\ntemp\n</file>',
        },
        {
            "role": "user",
            "content": "hi",
        },
        {
            "role": "system",
            "content": "[Synthetic context: _stage1_gather]\n[stage1 findings]",
        },
    ]


def test_chat_with_tools_keeps_real_tool_history_native_after_synthetic_textification(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "done"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="boot_ctx_0_0",
                    name="read_startup_context",
                    arguments={"file": "memory/agent/index.md"},
                )
            ],
        ),
        Message(
            role="tool",
            name="read_startup_context",
            tool_call_id="boot_ctx_0_0",
            content='<file path="memory/agent/index.md">\nindex\n</file>',
        ),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            reasoning_content="need to inspect the file first",
            tool_calls=[
                ToolCall(
                    id="legacy-tool-call",
                    name="read_file",
                    arguments={"path": "memory/agent/recent.md"},
                    provider_call_index=0,
                    provider_roundtrip={
                        "id": "legacy-tool-call",
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "memory/agent/recent.md"},
                            "index": 0,
                        },
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="legacy-tool-call",
            content="recent context",
        ),
    ]

    result = client.chat_with_tools(messages, [])

    assert result.content == "done"
    assert calls[0]["json"]["messages"] == [
        {
            "role": "system",
            "content": '[Synthetic context: read_startup_context]\n<file path="memory/agent/index.md">\nindex\n</file>',
        },
        {
            "role": "user",
            "content": "hi",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "legacy-tool-call",
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "memory/agent/recent.md"},
                        "index": 0,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "recent context",
            "tool_name": "read_file",
        },
    ]


def test_chat_with_tools_drops_thinking_on_replay_without_thought_signature(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "done"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            reasoning_content="need to inspect the file first",
            tool_calls=[
                ToolCall(
                    id="legacy-tool-call",
                    name="read_file",
                    arguments={"path": "memory/agent/recent.md"},
                    provider_call_index=0,
                    provider_roundtrip={
                        "id": "legacy-tool-call",
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "memory/agent/recent.md"},
                            "index": 0,
                        },
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="legacy-tool-call",
            content="recent context",
        ),
    ]

    result = client.chat_with_tools(messages, [])

    assert result.content == "done"
    assert "thinking" not in calls[0]["json"]["messages"][1]
    assert calls[0]["json"]["messages"][1]["tool_calls"] == [
        {
            "id": "legacy-tool-call",
            "function": {
                "name": "read_file",
                "arguments": {"path": "memory/agent/recent.md"},
                "index": 0,
            },
        }
    ]


def test_chat_with_tools_keeps_thinking_on_replay_with_thought_signature(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "done"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="gemini-3-flash-preview",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            reasoning_content="need to inspect the file first",
            tool_calls=[
                ToolCall(
                    id="provider-call-1",
                    name="read_file",
                    arguments={"path": "memory/agent/recent.md"},
                    thought_signature="sig-abc",
                    provider_call_index=3,
                    provider_roundtrip={
                        "id": "provider-call-1",
                        "thoughtSignature": "sig-abc",
                        "providerExtra": "keep-me",
                        "function": {
                            "index": 3,
                            "name": "read_file",
                            "arguments": {"path": "memory/agent/recent.md"},
                        },
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="provider-call-1",
            content="recent context",
        ),
    ]

    result = client.chat_with_tools(messages, [])

    assert result.content == "done"
    assert calls[0]["json"]["messages"][1]["thinking"] == "need to inspect the file first"
    assert calls[0]["json"]["messages"][1]["tool_calls"] == [
        {
            "id": "provider-call-1",
            "thoughtSignature": "sig-abc",
            "providerExtra": "keep-me",
            "function": {
                "name": "read_file",
                "arguments": {"path": "memory/agent/recent.md"},
                "index": 3,
            },
        }
    ]


def test_chat_with_tools_serializes_history_without_provider_call_index(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "done"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="legacy-tool-call",
                    name="read_file",
                    arguments={"path": "memory/agent/recent.md"},
                )
            ],
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="legacy-tool-call",
            content="recent context",
        ),
    ]

    result = client.chat_with_tools(messages, [])

    assert result.content == "done"
    assert calls[0]["json"]["messages"][1]["tool_calls"] == [
        {
            "id": "legacy-tool-call",
            "function": {
                "name": "read_file",
                "arguments": {"path": "memory/agent/recent.md"},
            },
        }
    ]


def test_chat_with_tools_raises_when_tool_name_cannot_be_repaired(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "ok"},
        "done_reason": "stop",
    }
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
    )

    with pytest.raises(ValueError, match="Message.name"):
        client.chat_with_tools(
            [
                Message(
                    role="tool",
                    tool_call_id="tc1",
                    content="orphaned tool result",
                )
            ],
            [],
        )


def test_chat_raises_context_length_error_on_native_400(monkeypatch):
    payload = FakeResponse(
        {"error": "input exceeds context length"},
        status_code=400,
    )
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, payload, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="glm-5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=False,
        )
    )

    with pytest.raises(ContextLengthExceededError):
        client.chat([Message(role="user", content="hi")])


def test_chat_with_tools_raises_on_500_with_tool_history(monkeypatch):
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    server_500 = httpx.HTTPStatusError(
        "Server error",
        request=request,
        response=httpx.Response(500, request=request),
    )
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, server_500, calls)
    client = OllamaNativeClient(
        OllamaNativeConfig(
            provider="ollama",
            model="kimi-k2.5:cloud",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
            vision=True,
        )
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
                    arguments={"path": "memory/agent/recent.md"},
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
    assert any(message["role"] == "tool" for message in calls[0]["json"]["messages"])


def test_resolve_llm_config_loads_glm_5_cloud_profile():
    config = resolve_llm_config("llm/ollama/glm-5-cloud/thinking.yaml")

    assert isinstance(config, OllamaNativeConfig)
    assert config.model == "glm-5:cloud"
    assert config.thinking.mode == "toggle"


def test_resolve_llm_config_loads_qwen_35_397b_cloud_profile():
    config = resolve_llm_config("llm/ollama/qwen3.5-397b-cloud/thinking.yaml")

    assert isinstance(config, OllamaNativeConfig)
    assert config.model == "qwen3.5:397b-cloud"
    assert config.thinking.mode == "toggle"
    assert config.vision is True


def test_resolve_llm_config_loads_gpt_oss_cloud_profile():
    config = resolve_llm_config("llm/ollama/gpt-oss-20b-cloud/think-medium.yaml")

    assert isinstance(config, OllamaNativeConfig)
    assert config.model == "gpt-oss:20b-cloud"
    assert config.thinking.mode == "effort"
