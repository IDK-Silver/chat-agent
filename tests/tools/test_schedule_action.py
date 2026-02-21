"""Tests for schedule_action tool."""

from datetime import datetime, timedelta, timezone

import pytest

from chat_agent.agent.queue import PersistentPriorityQueue
from chat_agent.agent.adapters.scheduler import make_heartbeat_message
from chat_agent.tools.builtin.schedule_action import (
    SCHEDULE_ACTION_DEFINITION,
    create_schedule_action,
)


def _future_local(hours=1):
    """Return a future datetime string in Asia/Taipei local format."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Taipei")
    dt = datetime.now(tz) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M")


# ------------------------------------------------------------------
# Definition
# ------------------------------------------------------------------


class TestDefinition:
    def test_name(self):
        assert SCHEDULE_ACTION_DEFINITION.name == "schedule_action"

    def test_required(self):
        assert SCHEDULE_ACTION_DEFINITION.required == ["action"]

    def test_action_enum(self):
        assert SCHEDULE_ACTION_DEFINITION.parameters["action"].enum == [
            "add",
            "list",
            "remove",
        ]


# ------------------------------------------------------------------
# Add
# ------------------------------------------------------------------


class TestAdd:
    def test_success(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", reason="test reminder", trigger_spec=_future_local())
        assert "OK" in result
        items = q.scan_pending(channel="system")
        assert len(items) == 1
        assert "[SCHEDULED]" in items[0][1].content
        assert "test reminder" in items[0][1].content
        assert items[0][1].priority == 0

    def test_not_before_is_set(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="test", trigger_spec=_future_local(hours=2))
        items = q.scan_pending(channel="system")
        assert items[0][1].not_before is not None

    def test_goes_to_delayed_pool(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="test", trigger_spec=_future_local(hours=2))
        assert q.pending_count() == 0  # Not in mem queue
        with q._delayed_lock:
            assert len(q._delayed) == 1

    def test_missing_reason(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", trigger_spec=_future_local())
        assert "Error" in result

    def test_missing_trigger_spec(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", reason="test")
        assert "Error" in result

    def test_past_trigger_spec(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", reason="test", trigger_spec="2020-01-01T09:00")
        assert "Error" in result
        assert "future" in result

    def test_invalid_trigger_spec_format(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", reason="test", trigger_spec="not-a-date")
        assert "Error" in result

    def test_no_system_flag_in_metadata(self, tmp_path):
        """Agent-scheduled messages should NOT have system=True."""
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="test", trigger_spec=_future_local())
        items = q.scan_pending(channel="system")
        assert "system" not in items[0][1].metadata


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------


class TestList:
    def test_empty(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="list")
        assert "No pending" in result

    def test_shows_scheduled(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="meeting reminder", trigger_spec=_future_local())
        result = fn(action="list")
        assert "SCHEDULED" in result

    def test_shows_system_tag(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        hb = make_heartbeat_message(
            not_before=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        q.put(hb)
        fn = create_schedule_action(q)
        result = fn(action="list")
        assert "[system]" in result

    def test_multiple_items(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="first", trigger_spec=_future_local(1))
        fn(action="add", reason="second", trigger_spec=_future_local(2))
        result = fn(action="list")
        lines = result.strip().split("\n")
        assert len(lines) == 2


# ------------------------------------------------------------------
# Remove
# ------------------------------------------------------------------


class TestRemove:
    def test_remove_agent_scheduled(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        fn(action="add", reason="removeme", trigger_spec=_future_local(2))
        items = q.scan_pending(channel="system")
        assert len(items) == 1
        pending_id = items[0][0].name

        result = fn(action="remove", pending_id=pending_id)
        assert "OK" in result
        assert len(q.scan_pending(channel="system")) == 0

    def test_remove_system_heartbeat_blocked(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        hb = make_heartbeat_message(
            not_before=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        q.put(hb)
        items = q.scan_pending(channel="system")
        pending_id = items[0][0].name

        fn = create_schedule_action(q)
        result = fn(action="remove", pending_id=pending_id)
        assert "Error" in result
        assert "system" in result
        # Message should still be there
        assert len(q.scan_pending(channel="system")) == 1

    def test_remove_not_found(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="remove", pending_id="nonexistent.json")
        assert "Error" in result

    def test_remove_missing_pending_id(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="remove")
        assert "Error" in result


# ------------------------------------------------------------------
# Unknown action
# ------------------------------------------------------------------


class TestUnknownAction:
    def test_unknown(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="invalid")
        assert "Error" in result
