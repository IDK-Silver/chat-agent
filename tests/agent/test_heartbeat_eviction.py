"""Tests for silent heartbeat turn eviction from in-memory conversation."""

from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.schema import InboundMessage
from chat_agent.agent.turn_context import TurnContext
from chat_agent.context.conversation import Conversation


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
    core.adapters = {}
    core.run_turn = MagicMock()
    return core, q, conv, tc


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
