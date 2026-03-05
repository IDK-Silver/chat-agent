"""Tests for schedule_action tool."""

from datetime import datetime, timedelta, timezone

import pytest

from chat_agent.agent.queue import PersistentPriorityQueue
from chat_agent.agent.adapters.scheduler import make_heartbeat_message
from chat_agent.timezone_utils import now as tz_now, parse_timezone_spec
from chat_agent.tools.builtin.schedule_action import (
    SCHEDULE_ACTION_DEFINITION,
    create_schedule_action,
)


def _future_local(hours=1, timezone_name="UTC+8"):
    """Return a future datetime string in the configured local format."""
    tz = parse_timezone_spec(timezone_name)
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

    def test_supports_utc_offset_timezone_string(self, tmp_path):
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        result = fn(action="add", reason="offset tz", trigger_spec=_future_local(1, "UTC+8"))
        assert "OK" in result

    def test_aware_trigger_spec_normalised_to_app_tz(self, tmp_path):
        """A trigger_spec with explicit non-app offset should be normalised."""
        q = PersistentPriorityQueue(tmp_path / "q")
        fn = create_schedule_action(q)
        # Build a future UTC datetime string
        future_utc = (datetime.now(timezone.utc) + timedelta(hours=2))
        trigger = future_utc.isoformat()
        result = fn(action="add", reason="utc test", trigger_spec=trigger)
        assert "OK" in result
        items = q.scan_pending(channel="system")
        msg = items[0][1]
        # timestamp and not_before should be in app timezone (UTC+8)
        tz8 = parse_timezone_spec("UTC+8")
        assert msg.timestamp.utcoffset() == tz8.utcoffset(None)
        assert msg.not_before.utcoffset() == tz8.utcoffset(None)
        # Display time in content should be UTC+8 (= UTC + 8h)
        expected_local = future_utc.astimezone(tz8)
        expected_str = expected_local.strftime("%Y-%m-%d %H:%M")
        assert expected_str in msg.content


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
            not_before=tz_now() + timedelta(hours=2),
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
            not_before=tz_now() + timedelta(hours=2),
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
