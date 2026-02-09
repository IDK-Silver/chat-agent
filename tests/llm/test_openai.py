"""Tests for OpenAI provider reasoning payload mapping."""

from chat_agent.core.schema import OpenAIConfig, ReasoningConfig
from chat_agent.llm.providers.openai import OpenAIClient
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
        "chat_agent.llm.providers.openai.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload, calls),
    )


def _make_payload(content: str = "ok") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_chat_includes_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
            reasoning=ReasoningConfig(effort="high"),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning_effort"] == "high"


def test_chat_with_tools_uses_override_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("done"), calls)
    client = OpenAIClient(
        OpenAIConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
            reasoning=ReasoningConfig(enabled=False),
            provider_overrides={"openai_reasoning_effort": "low"},
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
    _ = client.chat_with_tools([Message(role="user", content="hello")], tools)

    assert calls[0]["json"]["reasoning_effort"] == "low"
    assert "tools" in calls[0]["json"]
