"""Tests for SchedulerAdapter."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chat_agent.agent.adapters.scheduler import (
    SchedulerAdapter,
    make_heartbeat_message,
    parse_interval,
    random_delay,
)
from chat_agent.agent.schema import InboundMessage


# ------------------------------------------------------------------
# Interval parsing
# ------------------------------------------------------------------


class TestParseInterval:
    def test_valid(self):
        assert parse_interval("2h-5h") == (2, 5)

    def test_single_hour(self):
        assert parse_interval("1h-1h") == (1, 1)

    def test_swapped_order(self):
        assert parse_interval("5h-2h") == (2, 5)

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid interval"):
            parse_interval("bad")

    def test_invalid_no_h(self):
        with pytest.raises(ValueError):
            parse_interval("2-5")

    def test_invalid_empty(self):
        with pytest.raises(ValueError):
            parse_interval("")


class TestRandomDelay:
    def test_within_range(self):
        for _ in range(20):
            d = random_delay("2h-5h")
            assert timedelta(hours=2) <= d <= timedelta(hours=5)

    def test_same_bounds(self):
        d = random_delay("3h-3h")
        assert d == timedelta(hours=3)


# ------------------------------------------------------------------
# Heartbeat message creation
# ------------------------------------------------------------------


class TestMakeHeartbeatMessage:
    def test_startup_content(self):
        msg = make_heartbeat_message(is_startup=True)
        assert "[STARTUP]" in msg.content
        assert msg.channel == "system"
        assert msg.priority == 5
        assert msg.sender == "system"
        assert msg.not_before is None

    def test_startup_metadata(self):
        msg = make_heartbeat_message(is_startup=True, interval_spec="3h-6h")
        assert msg.metadata["system"] is True
        assert msg.metadata["recurring"] is True
        assert msg.metadata["recur_spec"] == "3h-6h"

    def test_regular_heartbeat_content(self):
        from datetime import datetime, timezone

        nb = datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc)
        msg = make_heartbeat_message(not_before=nb)
        assert "[HEARTBEAT]" in msg.content
        assert "2026-03-01 14:37" in msg.content
        assert msg.not_before == nb

    def test_regular_heartbeat_metadata(self):
        msg = make_heartbeat_message(interval_spec="1h-2h")
        assert msg.metadata["recurring"] is True
        assert msg.metadata["recur_spec"] == "1h-2h"


# ------------------------------------------------------------------
# Adapter start
# ------------------------------------------------------------------


class TestSchedulerAdapterStart:
    def _make_agent(self, pending_items=None):
        agent = MagicMock()
        agent._queue = MagicMock()
        agent._queue.scan_pending.return_value = pending_items or []
        return agent

    def test_clears_old_system_heartbeats(self):
        old_hb = make_heartbeat_message()
        agent = self._make_agent(
            pending_items=[
                (Path("/fake/pending/0005_00000001.json"), old_hb),
            ]
        )
        adapter = SchedulerAdapter(interval="2h-5h")
        adapter.start(agent)

        agent._queue.remove_pending.assert_called_once_with(
            Path("/fake/pending/0005_00000001.json")
        )

    def test_preserves_non_system_messages(self):
        non_system = InboundMessage(
            channel="system",
            content="[SCHEDULED]",
            priority=0,
            sender="system",
            metadata={},  # No "system" key
        )
        agent = self._make_agent(
            pending_items=[
                (Path("/fake/pending/0000_00000001.json"), non_system),
            ]
        )
        adapter = SchedulerAdapter(interval="2h-5h")
        adapter.start(agent)

        agent._queue.remove_pending.assert_not_called()

    def test_enqueues_startup_heartbeat(self):
        agent = self._make_agent()
        adapter = SchedulerAdapter(interval="3h-6h")
        adapter.start(agent)

        agent.enqueue.assert_called_once()
        enqueued = agent.enqueue.call_args[0][0]
        assert isinstance(enqueued, InboundMessage)
        assert "[STARTUP]" in enqueued.content
        assert enqueued.metadata["recur_spec"] == "3h-6h"
        assert enqueued.not_before is None

    def test_no_queue_no_crash(self):
        agent = MagicMock()
        agent._queue = None
        adapter = SchedulerAdapter()
        adapter.start(agent)  # Should not raise


# ------------------------------------------------------------------
# Protocol methods
# ------------------------------------------------------------------


class TestSchedulerAdapterProtocol:
    def test_channel_name(self):
        assert SchedulerAdapter().channel_name == "system"

    def test_priority(self):
        assert SchedulerAdapter().priority == 5

    def test_send_is_noop(self):
        adapter = SchedulerAdapter()
        adapter.send(MagicMock())  # No-op, no error

    def test_turn_callbacks_are_noop(self):
        adapter = SchedulerAdapter()
        adapter.on_turn_start("cli")
        adapter.on_turn_complete()
        adapter.stop()
