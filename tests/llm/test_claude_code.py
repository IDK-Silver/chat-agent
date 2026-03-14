"""Tests for the Claude Code provider client."""

from __future__ import annotations

from chat_agent.core.schema import ClaudeCodeConfig, ClaudeCodeThinkingConfig
from chat_agent.llm.providers.claude_code import ClaudeCodeClient
from chat_agent.llm.schema import ContentPart, Message, ToolDefinition, ToolParameter


class _SyncResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    def json(self) -> dict:
        return self._payload


class _SyncClient:
    def __init__(self, effects: list[dict], calls: list[dict]):
        self._effects = effects
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, headers: dict, json: dict):
        self._calls.append({"url": url, "headers": headers, "json": json})
        return _SyncResponse(self._effects.pop(0))


def _patch_sync_httpx(monkeypatch, effects: list[dict], calls: list[dict]) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.claude_code.httpx.Client",
        lambda timeout: _SyncClient(effects, calls),
    )


def test_claude_code_client_preserves_system_blocks_and_cache_control(monkeypatch):
    effects = [{"content": [{"type": "text", "text": "ok"}]}]
    calls: list[dict] = []
    _patch_sync_httpx(monkeypatch, effects, calls)
    client = ClaudeCodeClient(
        ClaudeCodeConfig(
            model="claude-sonnet-4-6",
            base_url="http://localhost:4142",
        )
    )

    response = client.chat(
        [
            Message(
                role="system",
                content=[
                    ContentPart(
                        type="text",
                        text="[Core Rules]",
                        cache_control={"type": "ephemeral"},
                    )
                ],
            ),
            Message(role="system", content="Runtime context"),
            Message(
                role="user",
                content=[
                    ContentPart(
                        type="text",
                        text="hello",
                        cache_control={"type": "ephemeral"},
                    )
                ],
            ),
        ]
    )

    assert response == "ok"
    payload = calls[0]["json"]
    assert calls[0]["url"] == "http://localhost:4142/v1/messages"
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["system"][1] == {"type": "text", "text": "Runtime context"}
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_claude_code_client_sends_thinking_and_tools(monkeypatch):
    effects = [{
        "content": [{"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "x"}}]
    }]
    calls: list[dict] = []
    _patch_sync_httpx(monkeypatch, effects, calls)
    client = ClaudeCodeClient(
        ClaudeCodeConfig(
            model="claude-sonnet-4-6",
            base_url="http://localhost:4142",
            reasoning=ClaudeCodeThinkingConfig(enabled=True, max_tokens=1024),
            temperature=0.2,
        )
    )

    response = client.chat_with_tools(
        [Message(role="user", content="inspect")],
        [
            ToolDefinition(
                name="read_file",
                description="Read a file",
                parameters={"path": ToolParameter(type="string", description="Path")},
                required=["path"],
            )
        ],
    )

    payload = calls[0]["json"]
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert "temperature" not in payload
    assert payload["tools"][0]["name"] == "read_file"
    assert response.tool_calls[0].name == "read_file"
