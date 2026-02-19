"""Tests for AgentCore queue-based methods."""

from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.schema import InboundMessage, OutboundMessage, ShutdownSentinel



class TestEnqueueAndShutdown:
    """Test enqueue / request_shutdown."""

    def test_enqueue_without_queue_raises(self):
        from chat_agent.agent.core import AgentCore

        core = AgentCore.__new__(AgentCore)
        core._queue = None
        with pytest.raises(RuntimeError, match="No queue configured"):
            core.enqueue(InboundMessage(channel="cli", content="x", priority=0, sender="u"))

    def test_request_shutdown_pushes_sentinel(self, tmp_path):
        from chat_agent.agent.queue import PersistentPriorityQueue
        from chat_agent.agent.core import AgentCore

        q = PersistentPriorityQueue(tmp_path / "q")
        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core.request_shutdown(graceful=True)

        msg, receipt = q.get()
        assert isinstance(msg, ShutdownSentinel)
        assert msg.graceful is True
        assert receipt is None  # sentinel not persisted


class TestRun:
    """Test AgentCore.run() loop."""

    def test_run_stops_on_graceful_sentinel(self, tmp_path):
        from chat_agent.agent.queue import PersistentPriorityQueue
        from chat_agent.agent.core import AgentCore

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(ShutdownSentinel(graceful=True))

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core.graceful_exit = MagicMock()

        core.run()
        core.graceful_exit.assert_called_once()

    def test_run_stops_on_non_graceful_sentinel(self, tmp_path):
        from chat_agent.agent.queue import PersistentPriorityQueue
        from chat_agent.agent.core import AgentCore

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(ShutdownSentinel(graceful=False))

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core.graceful_exit = MagicMock()

        core.run()
        core.graceful_exit.assert_not_called()

    def test_run_processes_message_then_stops(self, tmp_path):
        from chat_agent.agent.queue import PersistentPriorityQueue
        from chat_agent.agent.core import AgentCore

        q = PersistentPriorityQueue(tmp_path / "q")
        msg = InboundMessage(channel="cli", content="test", priority=0, sender="u")
        q.put(msg)

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {}
        core.graceful_exit = MagicMock()

        processed = []

        def fake_process(m, receipt):
            processed.append(m.content)
            # After processing, signal shutdown
            q.put(ShutdownSentinel(graceful=False))

        core._process_inbound = fake_process

        core.run()
        assert processed == ["test"]

    def test_run_starts_and_stops_adapters(self, tmp_path):
        from chat_agent.agent.queue import PersistentPriorityQueue
        from chat_agent.agent.core import AgentCore

        q = PersistentPriorityQueue(tmp_path / "q")
        q.put(ShutdownSentinel(graceful=False))

        adapter = MagicMock()
        adapter.channel_name = "cli"

        core = AgentCore.__new__(AgentCore)
        core._queue = q
        core.adapters = {"cli": adapter}
        core.graceful_exit = MagicMock()

        core.run()
        adapter.start.assert_called_once_with(core)
        adapter.stop.assert_called_once()
