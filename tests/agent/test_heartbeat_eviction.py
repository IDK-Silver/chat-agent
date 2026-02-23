"""Tests for system turn eviction from in-memory conversation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.schema import InboundMessage
from chat_agent.agent.turn_context import TurnContext
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ToolCall


def _make_system_heartbeat(**overrides):
    """Create a system heartbeat InboundMessage."""
    defaults = dict(
        channel="system",
        content="[HEARTBEAT]\nTime: 2026-02-21 12:00\n\nCheck memory.",
        priority=5,
        sender="system",
        metadata={"system": True, "recurring": True, "recur_spec": "3m-5m"},
    )
    defaults.update(overrides)
    return InboundMessage(**defaults)


def _make_scheduled_message(**overrides):
    """Create a scheduled system InboundMessage."""
    defaults = dict(
        channel="system",
        content=(
            "[SCHEDULED]\n"
            "Reason: follow up\n"
            "Scheduled at: 2026-02-23 21:20\n\n"
            "Act on this reason. Use send_message to deliver messages."
        ),
        priority=0,
        sender="system",
        metadata={"scheduled_reason": "follow up"},
    )
    defaults.update(overrides)
    return InboundMessage(**defaults)


def _make_core(tmp_path, *, turn_context=None):
    """Create a minimal AgentCore for _process_inbound testing."""
    from chat_agent.agent.core import AgentCore
    from chat_agent.agent.queue import PersistentPriorityQueue

    q = PersistentPriorityQueue(tmp_path / "q")
    conv = Conversation()
    tc = turn_context if turn_context is not None else TurnContext()

    core = AgentCore.__new__(AgentCore)
    core._queue = q
    core.console = MagicMock()
    core.conversation = conv
    core.turn_context = tc
    core.builder = MagicMock()
    core.adapters = {}
    core.run_turn = MagicMock()
    return core, q, conv, tc


def _add_tool_round(
    conv: Conversation,
    *,
    tool_calls: list[ToolCall],
    results: dict[str, str],
    content: str | None = None,
) -> None:
    """Append assistant tool calls and matching tool results to conversation."""
    conv.add_assistant_with_tools(content, tool_calls)
    for tc in tool_calls:
        conv.add_tool_result(tc.id, tc.name, results[tc.id])


class TestSilentHeartbeatEviction:
    """Silent system heartbeats should be evicted from in-memory conversation."""

    def test_silent_heartbeat_evicted(self, tmp_path):
        """A system heartbeat that sends nothing is removed from conversation."""
        core, q, conv, tc = _make_core(tmp_path)

        # Simulate existing user conversation
        conv.add("user", "hello", channel="cli", sender="alice")
        conv.add("assistant", "hi there")
        pre_count = len(conv.get_messages())  # 2

        # run_turn adds messages during the heartbeat turn
        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            conv.add("assistant", "nothing to do")

        core.run_turn.side_effect = fake_turn

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        # Heartbeat turn should be evicted; only original messages remain
        assert len(conv.get_messages()) == pre_count

    def test_active_heartbeat_preserved(self, tmp_path):
        """A system heartbeat that calls send_message is kept in conversation."""
        core, q, conv, tc = _make_core(tmp_path)

        conv.add("user", "hello", channel="cli", sender="alice")
        pre_count = len(conv.get_messages())

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            conv.add("assistant", "sending reminder")
            # Simulate send_message tool populating sent_hashes
            tc.check_sent_dedup("gmail", "alice", "reminder!")

        core.run_turn.side_effect = fake_turn

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        # Turn should be preserved (sent_hashes is non-empty)
        assert len(conv.get_messages()) > pre_count

    def test_non_system_message_never_evicted(self, tmp_path):
        """Regular user messages are never evicted even if sent_hashes is empty."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="cli", sender="alice")
            conv.add("assistant", "ok")

        core.run_turn.side_effect = fake_turn

        msg = InboundMessage(
            channel="cli", content="hi", priority=0, sender="alice",
        )
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        # Should have the messages from the turn
        assert len(conv.get_messages()) == 2

    def test_failed_turn_not_evicted(self, tmp_path):
        """If run_turn raises, no eviction happens (completed=False)."""
        core, q, conv, tc = _make_core(tmp_path)

        conv.add("user", "hello", channel="cli", sender="alice")
        pre_count = len(conv.get_messages())

        core.run_turn.side_effect = RuntimeError("LLM error")

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()

        with pytest.raises(RuntimeError):
            core._process_inbound(msg, receipt)

        # No eviction; conversation unchanged
        assert len(conv.get_messages()) == pre_count

    def test_eviction_does_not_affect_queue_ack(self, tmp_path):
        """Queue ack and next heartbeat scheduling still happen after eviction."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")

        core.run_turn.side_effect = fake_turn

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()

        with patch.object(core, "_schedule_next_heartbeat") as mock_schedule:
            core._process_inbound(msg, receipt)

            # Turn was evicted
            assert len(conv.get_messages()) == 0
            # But next heartbeat was still scheduled
            mock_schedule.assert_called_once_with(msg)

    def test_no_turn_context_skips_eviction(self, tmp_path):
        """If turn_context is None, eviction is skipped (safety)."""
        core, q, conv, _ = _make_core(tmp_path)
        core.turn_context = None

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")

        core.run_turn.side_effect = fake_turn

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        # No eviction because turn_context is None
        assert len(conv.get_messages()) == 1

    def test_eviction_recomputes_context_char_estimate(self, tmp_path):
        """Ctx counter should be recomputed after silent heartbeat eviction."""
        from chat_agent.context.builder import ContextBuilder

        core, q, conv, tc = _make_core(tmp_path)
        builder = ContextBuilder(system_prompt="sys")
        core.builder = builder

        # Seed with existing conversation and a stale ctx estimate.
        conv.add("user", "hello", channel="cli", sender="alice")
        conv.add("assistant", "hi")
        expected_chars = builder.estimate_chars(conv)
        builder.last_total_chars = expected_chars + 999

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            conv.add("assistant", "nothing to do")

        core.run_turn.side_effect = fake_turn

        msg = _make_system_heartbeat()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        # Heartbeat turn is evicted; ctx estimate should match current conversation.
        assert len(conv.get_messages()) == 2
        assert builder.last_total_chars == expected_chars


class TestScheduledNoopEviction:
    """Scheduled system turns should evict only when truly no-op."""

    def test_scheduled_noop_evicted(self, tmp_path):
        """Scheduled turn with no send/tool side effects should be evicted."""
        core, q, conv, tc = _make_core(tmp_path)

        conv.add("user", "hello", channel="cli", sender="alice")
        pre_count = len(conv.get_messages())

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            conv.add("assistant", "Checked. Nothing to do.")

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) == pre_count

    def test_scheduled_list_only_evicted(self, tmp_path):
        """schedule_action list alone is informational and should be evicted."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_list = ToolCall(id="tc_list", name="schedule_action", arguments={"action": "list"})
            _add_tool_round(
                conv,
                tool_calls=[tc_list],
                results={"tc_list": "No pending scheduled actions."},
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) == 0

    def test_scheduled_list_plus_add_preserved(self, tmp_path):
        """list + successful add advances schedule state, so keep the turn."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_list = ToolCall(id="tc_list", name="schedule_action", arguments={"action": "list"})
            tc_add = ToolCall(
                id="tc_add",
                name="schedule_action",
                arguments={
                    "action": "add",
                    "reason": "take medicine",
                    "trigger_spec": "2026-02-23T23:00",
                },
            )
            _add_tool_round(
                conv,
                tool_calls=[tc_list, tc_add],
                results={
                    "tc_list": "No pending scheduled actions.",
                    "tc_add": "OK: scheduled at 2026-02-23 23:00 (1.0h from now)",
                },
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) > 0

    def test_scheduled_add_failure_evicted(self, tmp_path):
        """Failed schedule add has no durable effect and should be evicted."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_add = ToolCall(
                id="tc_add",
                name="schedule_action",
                arguments={"action": "add", "reason": "x", "trigger_spec": "bad"},
            )
            _add_tool_round(
                conv,
                tool_calls=[tc_add],
                results={"tc_add": "Error: invalid datetime format: 'bad'"},
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) == 0

    def test_scheduled_memory_edit_applied_preserved(self, tmp_path):
        """Applied memory_edit changes count as durable side effects."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_mem = ToolCall(
                id="tc_mem",
                name="memory_edit",
                arguments={"as_of": "2026-02-23T12:00:00Z", "turn_id": "t1", "requests": []},
            )
            result = json.dumps(
                {
                    "status": "ok",
                    "turn_id": "t1",
                    "applied": [
                        {
                            "request_id": "r1",
                            "status": "applied",
                            "path": "memory/agent/recent.md",
                        }
                    ],
                    "errors": [],
                    "warnings": [],
                }
            )
            _add_tool_round(
                conv,
                tool_calls=[tc_mem],
                results={"tc_mem": result},
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) > 0

    def test_scheduled_memory_edit_noop_evicted(self, tmp_path):
        """memory_edit with only noop/already_applied should be treated as no-op."""
        core, q, conv, tc = _make_core(tmp_path)

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_mem = ToolCall(
                id="tc_mem",
                name="memory_edit",
                arguments={"as_of": "2026-02-23T12:00:00Z", "turn_id": "t1", "requests": []},
            )
            result = json.dumps(
                {
                    "status": "failed",
                    "turn_id": "t1",
                    "applied": [
                        {
                            "request_id": "r1",
                            "status": "noop",
                            "path": "memory/agent/recent.md",
                        },
                        {
                            "request_id": "r2",
                            "status": "already_applied",
                            "path": "memory/agent/pending-thoughts.md",
                        },
                    ],
                    "errors": [
                        {"request_id": "r3", "code": "x", "detail": "failed"}
                    ],
                    "warnings": [],
                }
            )
            _add_tool_round(
                conv,
                tool_calls=[tc_mem],
                results={"tc_mem": result},
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) == 0

    def test_scheduled_no_turn_context_skips_eviction(self, tmp_path):
        """Scheduled eviction is skipped when turn_context is unavailable."""
        core, q, conv, _ = _make_core(tmp_path)
        core.turn_context = None

        def fake_turn(content, **kwargs):
            conv.add("user", content, channel="system", sender="system")
            tc_list = ToolCall(id="tc_list", name="schedule_action", arguments={"action": "list"})
            _add_tool_round(
                conv,
                tool_calls=[tc_list],
                results={"tc_list": "No pending scheduled actions."},
            )

        core.run_turn.side_effect = fake_turn

        msg = _make_scheduled_message()
        q.put(msg)
        _, receipt = q.get()
        core._process_inbound(msg, receipt)

        assert len(conv.get_messages()) > 0
