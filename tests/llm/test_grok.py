"""Tests for Grok provider reasoning payload mapping and proxy routing."""

from lincy.core.schema import GrokConfig, GrokReasoningConfig
from lincy.llm.providers.grok import X_GROK_CONV_ID_HEADER, GrokClient
from lincy.llm.schema import Message

from .conftest import FakeHttpxClient, make_openai_payload


def _patch_httpx_client(monkeypatch, payload: dict, calls: list[dict]) -> None:
    monkeypatch.setattr(
        "lincy.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([payload], calls),
    )


def test_chat_includes_reasoning_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = GrokClient(
        GrokConfig(
            provider="grok",
            model="grok-4.5",
            reasoning=GrokReasoningConfig(effort="high"),
        )
    )

    result = client.chat([Message(role="user", content="hello")])

    assert result == "ok"
    assert calls[0]["url"] == "http://localhost:4144/v1/chat/completions"
    assert calls[0]["json"]["reasoning_effort"] == "high"
    assert calls[0]["json"]["model"] == "grok-4.5"
    assert calls[0]["headers"]["Authorization"] == "Bearer local-proxy"
    assert X_GROK_CONV_ID_HEADER not in calls[0]["headers"]


def test_chat_sends_x_grok_conv_id_for_cache_routing(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = GrokClient(
        GrokConfig(provider="grok", model="grok-4.5"),
        conv_id_provider=lambda: "session-1:brain:2026050112",
    )

    _ = client.chat([Message(role="user", content="hello")])

    assert calls[0]["headers"][X_GROK_CONV_ID_HEADER] == "session-1:brain:2026050112"


def test_chat_omits_conv_id_when_provider_returns_none(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = GrokClient(
        GrokConfig(provider="grok", model="grok-4.5"),
        conv_id_provider=lambda: None,
    )

    _ = client.chat([Message(role="user", content="hello")])

    assert X_GROK_CONV_ID_HEADER not in calls[0]["headers"]


def test_chat_enabled_false_sends_none_effort(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = GrokClient(
        GrokConfig(
            provider="grok",
            model="grok-4.3",
            reasoning=GrokReasoningConfig(enabled=False),
        )
    )

    _ = client.chat([Message(role="user", content="hello")])

    assert calls[0]["json"]["reasoning_effort"] == "none"


def test_chat_merges_leading_system_messages(monkeypatch):
    calls: list[dict] = []
    _patch_httpx_client(monkeypatch, make_openai_payload("ok"), calls)
    client = GrokClient(
        GrokConfig(provider="grok", model="grok-4.5"),
    )

    _ = client.chat(
        [
            Message(role="system", content="A"),
            Message(role="system", content="B"),
            Message(role="user", content="hi"),
        ]
    )

    messages = calls[0]["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "A\n\nB"
    assert messages[1]["role"] == "user"
