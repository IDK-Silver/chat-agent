"""Tests for Ollama provider behavior."""

from chat_agent.core.schema import OllamaConfig
from chat_agent.llm.providers.ollama import OllamaClient
from chat_agent.llm.schema import Message


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _FakeHttpxClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, json: dict) -> _FakeResponse:
        return _FakeResponse(self.payload)


def _patch_httpx_client(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        "chat_agent.llm.providers.ollama.httpx.Client",
        lambda timeout: _FakeHttpxClient(payload),
    )


def test_chat_returns_content_when_present(monkeypatch):
    payload = {
        "message": {
            "role": "assistant",
            "content": '{"passed": true, "violations": [], "guidance": ""}',
            "thinking": "internal reasoning",
        }
    }
    _patch_httpx_client(monkeypatch, payload)
    client = OllamaClient(OllamaConfig(provider="ollama", model="kimi-k2.5:cloud"))

    result = client.chat([Message(role="user", content="hi")])

    assert result == '{"passed": true, "violations": [], "guidance": ""}'


def test_chat_falls_back_to_thinking_when_content_empty(monkeypatch):
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": '```json\n{"passed": true, "violations": [], "guidance": ""}\n```',
        }
    }
    _patch_httpx_client(monkeypatch, payload)
    client = OllamaClient(OllamaConfig(provider="ollama", model="kimi-k2.5:cloud"))

    result = client.chat([Message(role="user", content="hi")])

    assert "passed" in result
