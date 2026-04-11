"""Tests for macOS personal-app tool helpers."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chat_agent.tools.builtin.macos_apps import (
    MacOSAppBridge,
    create_calendar_tool,
    create_notes_tool,
    create_photos_tool,
    create_reminders_tool,
)


def test_calendar_tool_get_delegates_to_bridge():
    bridge = MagicMock()
    bridge.calendar_get.return_value = {"ok": True, "event": {"uid": "evt-1"}}

    tool = create_calendar_tool(bridge)
    payload = json.loads(tool(action="get", event_uid="evt-1"))

    assert payload["event"]["uid"] == "evt-1"
    bridge.calendar_get.assert_called_once_with(event_uid="evt-1", calendar=None)


def test_calendar_tool_conflicts_requires_range():
    bridge = MagicMock()
    tool = create_calendar_tool(bridge)

    result = tool(action="conflicts", start="2026-04-20T15:00")

    assert result == "Error: 'start' and 'end' are required for conflicts"
    bridge.calendar_conflicts.assert_not_called()


def test_calendar_tool_rejects_invalid_time_range():
    bridge = MagicMock()
    tool = create_calendar_tool(bridge)

    result = tool(
        action="create",
        calendar="Work",
        title="Lecture",
        start="2026-04-20T15:00",
        end="2026-04-20T14:00",
    )

    assert result == "Error: 'end' must be after or equal to 'start'"
    bridge.calendar_create.assert_not_called()


def test_reminders_tool_get_requires_id():
    bridge = MagicMock()
    tool = create_reminders_tool(bridge)

    result = tool(action="get")

    assert result == "Error: 'reminder_id' is required for get"
    bridge.reminders_get.assert_not_called()


def test_notes_tool_create_requires_explicit_folder():
    bridge = MagicMock()
    tool = create_notes_tool(bridge)

    result = tool(action="create", body="hello")

    assert result == "Error: 'folder_id' or 'folder_path' is required for create"
    bridge.notes_create.assert_not_called()


def test_notes_tool_move_requires_target_folder():
    bridge = MagicMock()
    tool = create_notes_tool(bridge)

    result = tool(action="move", note_id="note-1")

    assert result == "Error: 'target_folder_id' or 'target_folder_path' is required for move"
    bridge.notes_move.assert_not_called()


def test_photos_tool_get_album_requires_album_id():
    bridge = MagicMock()
    tool = create_photos_tool(bridge)

    result = tool(action="get_album")

    assert result == "Error: 'album_id', 'album_name', or 'album_path' is required for get_album"


def test_photos_tool_get_media_requires_ids():
    bridge = MagicMock()
    tool = create_photos_tool(bridge)

    result = tool(action="get_media")

    assert result == "Error: 'media_ids' is required for get_media"
    bridge.photos_get_media.assert_not_called()


def test_prepare_export_dir_uses_default_root(tmp_path: Path):
    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=1,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
    )

    export_dir = bridge._prepare_export_dir(None)

    assert export_dir.is_absolute()
    export_dir.relative_to(tmp_path.resolve())


def test_prepare_export_dir_rejects_path_outside_allowed_paths(tmp_path: Path):
    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=1,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
    )

    with pytest.raises(ValueError, match="outside allowed paths"):
        bridge._prepare_export_dir("/etc")
