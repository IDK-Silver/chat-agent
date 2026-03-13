"""Tests for Copilot runtime routing and provider kwargs plumbing."""

import inspect

import pytest

from chat_agent.agent.schema import InboundMessage
from chat_agent.core.schema import (
    CopilotConfig,
    CopilotInboundRuleConfig,
    CopilotInitiatorPolicyConfig,
    OllamaNativeConfig,
    OllamaNativeToggleThinkingConfig,
)
from chat_agent.llm.factory import create_client
from chat_agent.llm.providers.copilot_runtime import CopilotRuntime


def test_copilot_create_client_accepts_runtime_and_dispatch_mode(monkeypatch):
    monkeypatch.setattr(
        "chat_agent.llm.providers.copilot.httpx.Client",
        lambda timeout: None,
    )
    runtime = CopilotRuntime(CopilotInitiatorPolicyConfig())
    client = create_client(
        CopilotConfig(model="test-model"),
        runtime=runtime,
        dispatch_mode="always_agent",
    )
    assert client._runtime is runtime
    assert client._dispatch_mode == "always_agent"


def test_non_copilot_rejects_dispatch_mode():
    config = OllamaNativeConfig(
        model="test-model",
        thinking=OllamaNativeToggleThinkingConfig(mode="toggle", enabled=True),
    )
    with pytest.raises(TypeError):
        create_client(config, dispatch_mode="always_agent")


def test_factory_has_no_provider_imports():
    import chat_agent.llm.factory as factory_module

    source = inspect.getsource(factory_module)
    assert "CopilotConfig" not in source
    assert "CopilotClient" not in source
    assert "isinstance" not in source


def test_runtime_uses_user_once_for_human_inbound():
    runtime = CopilotRuntime(CopilotInitiatorPolicyConfig())
    inbound = InboundMessage(
        channel="cli",
        content="hi",
        priority=0,
        sender="tester",
    )

    with runtime.inbound_scope(inbound):
        first = runtime.resolve_request("first_user_then_agent")
        second = runtime.resolve_request("first_user_then_agent")
        subagent = runtime.resolve_request("always_agent")

    assert first.initiator == "user"
    assert second.initiator == "agent"
    assert subagent.initiator == "agent"
    assert first.interaction_id == second.interaction_id == subagent.interaction_id


def test_runtime_forces_agent_for_internal_channels():
    runtime = CopilotRuntime(CopilotInitiatorPolicyConfig())
    inbound = InboundMessage(
        channel="gui",
        content="[GUI Task Result]",
        priority=0,
        sender="system",
        metadata={"gui_intent": "click button"},
    )

    with runtime.inbound_scope(inbound):
        routing = runtime.resolve_request("first_user_then_agent")

    assert routing.initiator == "agent"


def test_runtime_supports_custom_human_entry_rule():
    runtime = CopilotRuntime(
        CopilotInitiatorPolicyConfig(
            use_default_human_entry_rules=False,
            human_entry_rules=[
                CopilotInboundRuleConfig(
                    channel="discord",
                    metadata_equals={"source": "dm_immediate"},
                )
            ],
        )
    )
    inbound = InboundMessage(
        channel="discord",
        content="hi",
        priority=0,
        sender="tester",
        metadata={"source": "dm_immediate"},
    )

    with runtime.inbound_scope(inbound):
        routing = runtime.resolve_request("first_user_then_agent")

    assert routing.initiator == "user"
