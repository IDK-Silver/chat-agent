"""Tests for token-only soft limit compaction and overflow retry behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ContextLengthExceededError, LLMResponse


def _seed_turns(conv: Conversation, count: int) -> None:
    for i in range(count):
        conv.add("user", f"user-{i}")
        conv.add("assistant", f"assistant-{i}")


def _make_core(tmp_path, *, provider: str, preserve_turns: int = 2, soft_limit: int = 128_000, overflow_keep: int = 2):
    from chat_agent.agent.core import AgentCore, _LatestTokenStatus, _TurnTokenUsage
    from chat_agent.agent.turn_context import TurnContext

    core = AgentCore.__new__(AgentCore)
    core.client = MagicMock()
    core.sync_client = None
    core.conversation = Conversation()
    core.builder = ContextBuilder(system_prompt="sys", preserve_turns=preserve_turns)
    core.registry = MagicMock()
    core.registry.get_definitions.return_value = []
    core.ui_sink = MagicMock()
    core.console = MagicMock()
    core.console.debug = False
    core.workspace = MagicMock()
    core.config = SimpleNamespace(
        context=SimpleNamespace(
            common_ground=SimpleNamespace(enabled=False),
            preserve_turns=preserve_turns,
            soft_max_prompt_tokens=soft_limit,
            overflow_retry_keep_turns=overflow_keep,
        ),
        tools=SimpleNamespace(
            max_tool_iterations=3,
            memory_edit=SimpleNamespace(turn_retry_limit=1),
            memory_sync=SimpleNamespace(every_n_turns=None, max_retries=1),
        ),
        maintenance=SimpleNamespace(archive=SimpleNamespace()),
        agents={"brain": SimpleNamespace(llm=SimpleNamespace(provider=provider))},
    )
    core.agent_os_dir = tmp_path
    core.user_id = "user"
    core.session_mgr = MagicMock()
    core.display_name = "User"
    core.memory_edit_allow_failure = False
    core.memory_backup_mgr = None
    core._queue = None
    core.turn_context = TurnContext()
    core.turn_cancel = None
    core.shared_state_store = None
    core.scope_resolver = None
    core._maintenance_scheduler = None
    core._turns_since_memory_sync = 0
    core.adapters = {}
    core._brain_provider = provider
    core._soft_max_prompt_tokens = soft_limit
    core._latest_token_status = _LatestTokenStatus()
    core._turn_token_usage = _TurnTokenUsage()
    return core


def test_soft_limit_compacts_to_preserve_turns(monkeypatch, tmp_path):
    from chat_agent.agent import core as core_module

    core = _make_core(tmp_path, provider="openrouter", preserve_turns=2, soft_limit=128_000)
    _seed_turns(core.conversation, 4)

    def _fake_run_brain_responder(**kwargs):
        response = LLMResponse(
            content="ok",
            tool_calls=[],
            prompt_tokens=140_000,
            completion_tokens=80,
            total_tokens=140_080,
            usage_available=True,
        )
        cb = kwargs.get("on_model_response")
        if cb is not None:
            cb(response)
        return response

    monkeypatch.setattr(core_module, "_run_brain_responder", _fake_run_brain_responder)
    monkeypatch.setattr(core_module, "_run_memory_archive", lambda *args, **kwargs: None)

    core.run_turn("new message", output_fn=lambda _text: None, channel="cli", sender="tester")

    user_count = sum(1 for m in core.conversation.get_messages() if m.role == "user")
    assert user_count == 2
    assert core.session_mgr.rewrite_messages.called
    assert "soft-over" in core.get_token_status_text()


def test_copilot_missing_usage_shows_unavailable_and_skips_compaction(monkeypatch, tmp_path):
    from chat_agent.agent import core as core_module

    core = _make_core(tmp_path, provider="copilot", preserve_turns=2, soft_limit=128_000)
    _seed_turns(core.conversation, 3)

    def _fake_run_brain_responder(**kwargs):
        response = LLMResponse(content="ok", tool_calls=[], usage_available=False)
        cb = kwargs.get("on_model_response")
        if cb is not None:
            cb(response)
        return response

    monkeypatch.setattr(core_module, "_run_brain_responder", _fake_run_brain_responder)
    monkeypatch.setattr(core_module, "_run_memory_archive", lambda *args, **kwargs: None)

    core.run_turn("copilot turn", output_fn=lambda _text: None, channel="cli", sender="tester")

    user_count = sum(1 for m in core.conversation.get_messages() if m.role == "user")
    assert user_count > 2
    assert core.get_token_status_text() == "tok unavailable/128,000 (copilot no usage)"


def test_context_length_overflow_retries_once_with_emergency_compaction(monkeypatch, tmp_path):
    from chat_agent.agent import core as core_module

    core = _make_core(
        tmp_path,
        provider="openrouter",
        preserve_turns=4,
        soft_limit=128_000,
        overflow_keep=1,
    )
    _seed_turns(core.conversation, 5)
    calls = {"count": 0}

    def _fake_run_brain_responder(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ContextLengthExceededError("context length exceeded")
        response = LLMResponse(
            content="recovered",
            tool_calls=[],
            prompt_tokens=1000,
            completion_tokens=20,
            total_tokens=1020,
            usage_available=True,
        )
        cb = kwargs.get("on_model_response")
        if cb is not None:
            cb(response)
        return response

    monkeypatch.setattr(core_module, "_run_brain_responder", _fake_run_brain_responder)
    monkeypatch.setattr(core_module, "_run_memory_archive", lambda *args, **kwargs: None)

    core.run_turn("retry turn", output_fn=lambda _text: None, channel="cli", sender="tester")

    assert calls["count"] == 2
    user_count = sum(1 for m in core.conversation.get_messages() if m.role == "user")
    assert user_count <= 2
    assert core.session_mgr.rewrite_messages.called


def test_memory_sync_reminder_uses_rollup_instruction():
    from chat_agent.agent.core import _build_memory_sync_reminder

    text = _build_memory_sync_reminder(
        ["memory/agent/recent.md"],
        turns_accumulated=5,
    )

    assert "[MEMORY SYNC - ROLLUP]" in text
    assert "not been updated for 5 turns" in text
    assert "EXACTLY ONE rollup entry" in text
    assert "[rollup 5 turns]" in text


def test_memory_sync_side_channel_uses_brain_client(monkeypatch, tmp_path):
    from chat_agent.agent import core as core_module

    core = _make_core(tmp_path, provider="openrouter")
    core.config.tools.memory_sync.every_n_turns = 1
    core.sync_client = MagicMock(name="deprecated_sync_client")

    captured: dict[str, object] = {}

    def _fake_run_brain_responder(**kwargs):
        response = LLMResponse(
            content="ok",
            tool_calls=[],
            prompt_tokens=1000,
            completion_tokens=30,
            total_tokens=1030,
            usage_available=True,
        )
        cb = kwargs.get("on_model_response")
        if cb is not None:
            cb(response)
        return response

    def _fake_find_missing(_turn_messages):
        return ["memory/agent/recent.md"]

    def _fake_run_memory_sync_side_channel(client, *_args, **_kwargs):
        captured["client"] = client

    monkeypatch.setattr(core_module, "_run_brain_responder", _fake_run_brain_responder)
    monkeypatch.setattr(core_module, "find_missing_memory_sync_targets", _fake_find_missing)
    monkeypatch.setattr(
        core_module,
        "_run_memory_sync_side_channel",
        _fake_run_memory_sync_side_channel,
    )
    monkeypatch.setattr(core_module, "_run_memory_archive", lambda *args, **kwargs: None)

    core.run_turn("needs sync", output_fn=lambda _text: None, channel="cli", sender="tester")

    assert captured["client"] is core.client
