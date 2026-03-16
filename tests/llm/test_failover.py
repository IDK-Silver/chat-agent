"""Tests for generic agent-level LLM failover."""

import httpx
import pytest

from chat_agent.core.schema import AgentConfig, ClaudeCodeConfig, OpenRouterConfig
from chat_agent.llm.agent_factory import create_agent_client
from chat_agent.llm.failover import (
    FailoverCandidate,
    llm_failover_key,
    reset_failover_cooldowns,
    with_llm_failover,
)
from chat_agent.llm.schema import LLMResponse, Message


def _make_429(*, headers=None):
    request = httpx.Request("POST", "http://localhost:4142/v1/messages")
    return httpx.HTTPStatusError(
        "Rate limited",
        request=request,
        response=httpx.Response(429, request=request, headers=headers or {}),
    )


def _make_status(code: int, text: str = ""):
    request = httpx.Request("POST", "http://localhost:4142/v1/messages")
    return httpx.HTTPStatusError(
        f"HTTP {code}",
        request=request,
        response=httpx.Response(code, request=request, text=text),
    )


class _StubClient:
    def __init__(self, *, chat_effects=None, tool_effects=None):
        self.chat_effects = list(chat_effects or [])
        self.tool_effects = list(tool_effects or [])
        self.chat_calls = 0
        self.tool_calls_count = 0

    def chat(self, messages, response_schema=None, temperature=None):
        self.chat_calls += 1
        effect = self.chat_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def chat_with_tools(self, messages, tools, temperature=None):
        self.tool_calls_count += 1
        effect = self.tool_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest.fixture(autouse=True)
def _reset_global_failover_state():
    reset_failover_cooldowns()
    yield
    reset_failover_cooldowns()


def test_failover_uses_secondary_after_429():
    primary = _StubClient(
        tool_effects=[_make_429()],
    )
    fallback = _StubClient(
        tool_effects=[LLMResponse(content="ok", tool_calls=[])],
    )
    client = with_llm_failover(
        [
            FailoverCandidate(
                key="claude-primary",
                label="brain-primary",
                client=primary,
            ),
            FailoverCandidate(
                key="openrouter-fallback",
                label="brain-fallback",
                client=fallback,
            ),
        ],
        cooldown_seconds=1800,
        label="brain",
    )

    result = client.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.content == "ok"
    assert primary.tool_calls_count == 1
    assert fallback.tool_calls_count == 1


def test_failover_skips_cooled_primary_when_alternative_exists():
    primary_one = _StubClient(
        tool_effects=[_make_429()],
    )
    fallback_one = _StubClient(
        tool_effects=[LLMResponse(content="first", tool_calls=[])],
    )
    client_one = with_llm_failover(
        [
            FailoverCandidate("shared-claude", "primary-one", primary_one),
            FailoverCandidate("shared-openrouter", "fallback-one", fallback_one),
        ],
        cooldown_seconds=1800,
        label="brain",
    )
    assert client_one.chat_with_tools([Message(role="user", content="hi")], []).content == "first"

    primary_two = _StubClient(
        tool_effects=[LLMResponse(content="should-not-run", tool_calls=[])],
    )
    fallback_two = _StubClient(
        tool_effects=[LLMResponse(content="second", tool_calls=[])],
    )
    client_two = with_llm_failover(
        [
            FailoverCandidate("shared-claude", "primary-two", primary_two),
            FailoverCandidate("shared-openrouter", "fallback-two", fallback_two),
        ],
        cooldown_seconds=1800,
        label="memory_editor",
    )

    result = client_two.chat_with_tools([Message(role="user", content="hi")], [])

    assert result.content == "second"
    assert primary_two.tool_calls_count == 0
    assert fallback_two.tool_calls_count == 1


def test_failover_does_not_switch_on_request_format_error():
    primary = _StubClient(
        tool_effects=[
            _make_status(
                400,
                '{"error":"Function call is missing a thought_signature in functionCall parts."}',
            )
        ],
    )
    fallback = _StubClient(
        tool_effects=[LLMResponse(content="should-not-run", tool_calls=[])],
    )
    client = with_llm_failover(
        [
            FailoverCandidate("claude-primary", "primary", primary),
            FailoverCandidate("openrouter-fallback", "fallback", fallback),
        ],
        cooldown_seconds=1800,
        label="memory_editor",
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.chat_with_tools([Message(role="user", content="hi")], [])

    assert primary.tool_calls_count == 1
    assert fallback.tool_calls_count == 0


def test_failover_key_shares_quota_bucket_across_models():
    claude_opus = ClaudeCodeConfig(
        provider="claude_code",
        model="claude-opus-4-6",
        base_url="http://localhost:4142",
    )
    claude_sonnet = ClaudeCodeConfig(
        provider="claude_code",
        model="claude-sonnet-4-6",
        base_url="http://localhost:4142",
    )
    openrouter_sonnet = OpenRouterConfig(
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    openrouter_haiku = OpenRouterConfig(
        provider="openrouter",
        model="anthropic/claude-haiku-4.5",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )

    assert llm_failover_key(claude_opus) == llm_failover_key(claude_sonnet)
    assert llm_failover_key(openrouter_sonnet) == llm_failover_key(openrouter_haiku)


def test_agent_factory_skips_429_retries_before_fallback(monkeypatch):
    observed: list[dict[str, object]] = []

    def _fake_create_client(
        config,
        transient_retries=0,
        request_timeout=None,
        rate_limit_retries=0,
        retry_label=None,
        **provider_kwargs,
    ):
        observed.append({
            "model": config.model,
            "rate_limit_retries": rate_limit_retries,
            "retry_label": retry_label,
        })
        return _StubClient(tool_effects=[LLMResponse(content="ok", tool_calls=[])])

    monkeypatch.setattr("chat_agent.llm.agent_factory.create_client", _fake_create_client)

    agent_config = AgentConfig(
        llm=ClaudeCodeConfig(
            provider="claude_code",
            model="claude-sonnet-4-6",
            base_url="http://localhost:4142",
        ),
        llm_fallbacks=[
            OpenRouterConfig(
                provider="openrouter",
                model="anthropic/claude-sonnet-4.6",
                base_url="https://openrouter.ai/api/v1",
                api_key="test-key",
            )
        ],
        llm_rate_limit_retries=5,
    )

    create_agent_client(agent_config, retry_label="brain")

    assert observed == [
        {
            "model": "claude-sonnet-4-6",
            "rate_limit_retries": 0,
            "retry_label": "brain",
        },
        {
            "model": "anthropic/claude-sonnet-4.6",
            "rate_limit_retries": 5,
            "retry_label": "brain.fallback1",
        },
    ]
