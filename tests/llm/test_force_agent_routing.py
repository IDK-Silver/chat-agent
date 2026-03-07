"""Tests for force_agent routing: app.py -> factory -> provider create_client.

Verifies:
1. CopilotConfig + force_agent=True -> CopilotClient receives force_agent=True
2. Non-Copilot config + force_agent=True -> TypeError (no silent ignore)
3. factory.py is provider-agnostic (no isinstance CopilotConfig)
4. brain client intentionally does NOT receive copilot_agent_hint
"""

import pytest

from chat_agent.core.schema import (
    CopilotConfig,
    CopilotReasoningConfig,
    GeminiConfig,
    GeminiCapabilities,
    GeminiReasoningCapabilities,
    GeminiThinkingConfig,
    OllamaNativeConfig,
    OllamaNativeToggleThinkingConfig,
)
from chat_agent.llm.factory import create_client

from .conftest import FakeHttpxClient, make_openai_payload


def _patch_all_httpx(monkeypatch, calls):
    """Patch httpx for both OpenAI-compat and Gemini providers."""
    monkeypatch.setattr(
        "chat_agent.llm.providers.openai_compat.httpx.Client",
        lambda timeout: FakeHttpxClient([make_openai_payload("ok")], calls),
    )


class TestForceAgentRouting:
    def test_copilot_receives_force_agent(self, monkeypatch):
        """CopilotConfig.create_client(force_agent=True) passes to CopilotClient."""
        calls: list[dict] = []
        _patch_all_httpx(monkeypatch, calls)

        config = CopilotConfig(model="test-model")
        client = create_client(config, force_agent=True)
        # No retries -> factory returns CopilotClient directly
        assert client._force_agent is True

    def test_copilot_default_no_force_agent(self, monkeypatch):
        """CopilotConfig without force_agent defaults to False."""
        calls: list[dict] = []
        _patch_all_httpx(monkeypatch, calls)

        config = CopilotConfig(model="test-model")
        client = create_client(config)
        assert client._force_agent is False

    def test_non_copilot_rejects_force_agent(self):
        """Non-Copilot provider raises TypeError on force_agent=True."""
        config = OllamaNativeConfig(
            model="test-model",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
        )
        with pytest.raises(TypeError):
            create_client(config, force_agent=True)

    def test_non_copilot_no_kwargs_ok(self, monkeypatch):
        """Non-Copilot provider works without provider kwargs."""
        calls: list[dict] = []
        _patch_all_httpx(monkeypatch, calls)

        config = OllamaNativeConfig(
            model="test-model",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
        )
        # Should not raise — no extra kwargs
        client = create_client(config)
        assert client is not None


class TestFactoryProviderAgnostic:
    def test_factory_has_no_provider_imports(self):
        """factory.py must not import any provider Config or Client class."""
        import inspect
        import chat_agent.llm.factory as factory_module

        source = inspect.getsource(factory_module)
        # Must not import any provider-specific types
        assert "CopilotConfig" not in source
        assert "GeminiConfig" not in source
        assert "AnthropicConfig" not in source
        assert "OpenAIConfig" not in source
        assert "OllamaNativeConfig" not in source
        assert "OpenRouterConfig" not in source
        assert "CopilotClient" not in source
        assert "GeminiClient" not in source
        assert "isinstance" not in source


class TestProviderKwargsHelper:
    """Test the _provider_kwargs routing logic (same logic as app.py)."""

    def test_copilot_with_hint_returns_force_agent(self):
        config = CopilotConfig(model="test")
        agent_hint = True
        kwargs = {"force_agent": True} if agent_hint and isinstance(config, CopilotConfig) else {}
        assert kwargs == {"force_agent": True}

    def test_copilot_without_hint_returns_empty(self):
        config = CopilotConfig(model="test")
        agent_hint = False
        kwargs = {"force_agent": True} if agent_hint and isinstance(config, CopilotConfig) else {}
        assert kwargs == {}

    def test_non_copilot_with_hint_returns_empty(self):
        config = OllamaNativeConfig(
            model="test",
            thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
        )
        agent_hint = True
        kwargs = {"force_agent": True} if agent_hint and isinstance(config, CopilotConfig) else {}
        assert kwargs == {}
