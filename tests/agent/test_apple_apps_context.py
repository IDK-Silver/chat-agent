"""Tests for compact Calendar/Reminders note syncing."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from chat_agent.agent.apple_apps_context import AppleAppsContextSync
from chat_agent.agent.note_store import NoteStore
from chat_agent.core.schema import AppleAppsContextSyncConfig
from chat_agent.timezone_utils import get_tz


def _dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=get_tz())


class _FakeBridge:
    def __init__(self) -> None:
        self.calendar_calls: list[dict[str, object]] = []
        self.reminder_calls: list[dict[str, object]] = []

    def calendar_search(self, **kwargs):
        self.calendar_calls.append(kwargs)
        end = str(kwargs["end"])
        if end.startswith("2026-04-11T23:59:59"):
            return {
                "ok": True,
                "results": [
                    {
                        "uid": "evt-1",
                        "title": "專題會議",
                        "start": "2026-04-11T14:00:00+08:00",
                        "end": "2026-04-11T15:00:00+08:00",
                        "calendar": "工作",
                        "location": "3F 小會議室",
                        "all_day": False,
                    },
                    {
                        "uid": "evt-2",
                        "title": "晚餐",
                        "start": "2026-04-11T18:30:00+08:00",
                        "end": "2026-04-11T20:00:00+08:00",
                        "calendar": "個人",
                        "location": None,
                        "all_day": False,
                    },
                ],
            }
        return {
            "ok": True,
            "results": [
                {
                    "uid": "evt-1",
                    "title": "專題會議",
                    "start": "2026-04-11T14:00:00+08:00",
                    "end": "2026-04-11T15:00:00+08:00",
                    "calendar": "工作",
                    "location": "3F 小會議室",
                    "all_day": False,
                }
            ],
        }

    def reminders_search(self, **kwargs):
        self.reminder_calls.append(kwargs)
        due_end = str(kwargs["due_end"])
        if due_end.startswith("2026-04-11T23:59:59"):
            return {
                "ok": True,
                "results": [
                    {
                        "id": "rem-1",
                        "title": "寄講義",
                        "due": "2026-04-11T08:00:00+08:00",
                        "list_path": "iCloud/工作",
                    },
                    {
                        "id": "rem-2",
                        "title": "買咖啡",
                        "due": "2026-04-11T18:00:00+08:00",
                        "list_path": "iCloud/Errands",
                    },
                ],
            }
        return {
            "ok": True,
            "results": [
                {
                    "id": "rem-2",
                    "title": "買咖啡",
                    "due": "2026-04-11T18:00:00+08:00",
                    "list_path": "iCloud/Errands",
                }
            ],
        }


def test_apple_apps_context_sync_populates_managed_notes(tmp_path: Path):
    state_dir = tmp_path / "state"
    note_store = NoteStore(state_dir)
    bridge = _FakeBridge()
    sync = AppleAppsContextSync(
        bridge=bridge,
        note_store=note_store,
        state_dir=state_dir,
        sync_config=AppleAppsContextSyncConfig(
            enabled=True,
            cooldown_seconds=300,
            calendar_window_hours=36,
            calendar_max_events=5,
            reminders_window_days=7,
            reminders_max_items=6,
        ),
    )

    now = _dt(2026, 4, 11, 9, 30)
    with (
        patch("chat_agent.agent.apple_apps_context.tz_now", return_value=now),
        patch("chat_agent.agent.note_store.tz_now", return_value=now),
    ):
        result = sync.maybe_refresh(reason="test", force=True)

    assert result["refreshed"] is True
    next_event = note_store.get("calendar.next_event")
    assert next_event is not None
    assert next_event.source_app == "calendar"
    assert next_event.source_id == "evt-1"
    assert next_event.source_label == "next_event"
    assert "2026-04-11 14:00-15:00" in next_event.value
    assert "專題會議" in next_event.value
    assert "[工作]" in next_event.value

    today_summary = note_store.get("calendar.today_summary")
    assert today_summary is not None
    assert "專題會議 [工作]" in today_summary.value
    assert "晚餐 [個人]" in today_summary.value

    next_due = note_store.get("reminders.next_due")
    assert next_due is not None
    assert next_due.source_app == "reminders"
    assert next_due.source_id == "rem-2"
    assert next_due.source_label == "next_due"
    assert "today 18:00" in next_due.value
    assert "買咖啡" in next_due.value

    today_focus = note_store.get("reminders.today_focus")
    assert today_focus is not None
    assert "overdue since 2026-04-11 08:00" in today_focus.value
    assert "買咖啡" in today_focus.value

    context = note_store.format_context_block()
    assert context is not None
    assert "source calendar:next_event" in context
    assert "source reminders:today_focus" in context


def test_apple_apps_context_sync_respects_cooldown_and_preserves_note_timestamp_on_noop(
    tmp_path: Path,
):
    state_dir = tmp_path / "state"
    note_store = NoteStore(state_dir)
    bridge = _FakeBridge()
    sync = AppleAppsContextSync(
        bridge=bridge,
        note_store=note_store,
        state_dir=state_dir,
        sync_config=AppleAppsContextSyncConfig(
            enabled=True,
            cooldown_seconds=300,
            calendar_window_hours=36,
            calendar_max_events=5,
            reminders_window_days=7,
            reminders_max_items=6,
        ),
    )

    first_now = _dt(2026, 4, 11, 9, 30)
    second_now = _dt(2026, 4, 11, 9, 31)
    third_now = _dt(2026, 4, 11, 10, 0)

    with (
        patch("chat_agent.agent.apple_apps_context.tz_now", return_value=first_now),
        patch("chat_agent.agent.note_store.tz_now", return_value=first_now),
    ):
        sync.maybe_refresh(reason="first", force=True)
    first_updated_at = note_store.get("calendar.next_event").updated_at
    first_calendar_calls = len(bridge.calendar_calls)
    first_reminder_calls = len(bridge.reminder_calls)

    with (
        patch("chat_agent.agent.apple_apps_context.tz_now", return_value=second_now),
        patch("chat_agent.agent.note_store.tz_now", return_value=second_now),
    ):
        result = sync.maybe_refresh(reason="cooldown", force=False)
    assert result["refreshed"] is False
    assert len(bridge.calendar_calls) == first_calendar_calls
    assert len(bridge.reminder_calls) == first_reminder_calls

    with (
        patch("chat_agent.agent.apple_apps_context.tz_now", return_value=third_now),
        patch("chat_agent.agent.note_store.tz_now", return_value=third_now),
    ):
        sync.maybe_refresh(reason="force", force=True)
    assert note_store.get("calendar.next_event").updated_at == first_updated_at
