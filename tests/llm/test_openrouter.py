"""Tests for OpenRouter provider reasoning payload mapping."""

from chat_agent.core.schema import OpenRouterConfig, ReasoningConfig
from chat_agent.llm.providers.openrouter import OpenRouterClient
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
        "chat_agent.llm.providers.openrouter.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload, calls),
    )


def _make_payload(content: str = "ok") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_chat_includes_openrouter_reasoning_object(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("ok"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-pro-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(enabled=True, effort="high", max_tokens=2048),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["json"]["reasoning"] == {
        "enabled": True,
        "effort": "high",
        "max_tokens": 2048,
    }


def test_chat_with_tools_uses_openrouter_reasoning_override(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, _make_payload("done"), calls)
    client = OpenRouterClient(
        OpenRouterConfig(
            provider="openrouter",
            model="google/gemini-3-pro-preview",
            api_key="test-key",
            reasoning=ReasoningConfig(enabled=False),
            provider_overrides={"openrouter_reasoning": {"enabled": False}},
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

    assert calls[0]["json"]["reasoning"] == {"enabled": False}
    assert "tools" in calls[0]["json"]
