"""Tests for macOS personal-app tool helpers."""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chat_agent.tools.builtin.macos_apps import (
    MacOSAppBridge,
    _applescript_utf8_file_read,
    _ensure_note_title_html,
    _format_app_tool_log_details,
    _render_note_template_html,
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


def test_notes_tool_create_accepts_template_markdown():
    bridge = MagicMock()
    bridge.notes_create.return_value = {"ok": True}
    tool = create_notes_tool(bridge)

    result = json.loads(
        tool(
            action="create",
            folder_path="iCloud/待讀",
            template_markdown="# {paper_title}\n{image_cover}\n{summary}",
            variables={"paper_title": "多目標追蹤模型", "summary": "這是一篇摘要"},
            images={"image_cover": "/tmp/cover.png"},
        )
    )

    assert result["ok"] is True
    bridge.notes_create.assert_called_once_with(
        folder_id=None,
        folder_path="iCloud/待讀",
        title=None,
        body=None,
        template_markdown="# {paper_title}\n{image_cover}\n{summary}",
        variables={"paper_title": "多目標追蹤模型", "summary": "這是一篇摘要"},
        images={"image_cover": "/tmp/cover.png"},
    )


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


def test_run_applescript_utf8_files_preserves_non_ascii(tmp_path: Path):
    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=5,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
    )

    result = bridge._run_applescript(
        f"return {_applescript_utf8_file_read('NOTE_BODY')}\n",
        utf8_files={"NOTE_BODY": "中文測試abc"},
    )

    assert result == "中文測試abc"


def test_app_tool_log_details_redacts_text_but_keeps_scope_fields():
    result = _format_app_tool_log_details(
        {
            "folder_path": "iCloud/備忘錄",
            "title": "這是標題",
            "body": "這是內容",
            "query": "中文查詢",
            "limit": 25,
        }
    )

    assert "folder_path='iCloud/備忘錄'" in result
    assert "limit=25" in result
    assert "title_chars=4" in result
    assert "body_chars=4" in result
    assert "query_chars=4" in result
    assert "這是標題" not in result


def test_render_note_template_html_supports_custom_variables_and_images(tmp_path: Path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"png-test")

    html = _render_note_template_html(
        template_markdown=(
            "# {paper_title}\n"
            "來源：{url}\n\n"
            "## 原圖\n"
            "{image_cover}\n\n"
            "## 重點\n"
            "- {point_1}\n"
            "- {point_2}\n\n"
            "|欄位|值|\n"
            "|---|---|\n"
            "|作者|{author_name}|"
        ),
        variables={
            "paper_title": "多目標追蹤模型",
            "url": "https://x.com/example",
            "point_1": "支援任意檢測器",
            "point_2": "CLI 一行追蹤影片",
            "author_name": "Berryxia",
        },
        images={"image_cover": str(image_path)},
        allowed_paths=[str(tmp_path)],
        base_dir=tmp_path,
    )

    assert "<h1>多目標追蹤模型</h1>" in html
    assert "<h2>原圖</h2>" in html
    assert "data:image/png;base64," in html
    assert "<ul><li>支援任意檢測器</li><li>CLI 一行追蹤影片</li></ul>" in html
    assert "<table>" in html
    assert "Berryxia" in html


def test_render_note_template_html_supports_markdown_image_placeholder(tmp_path: Path):
    image_path = tmp_path / "cover.jpg"
    image_path.write_bytes(b"jpg-test")

    html = _render_note_template_html(
        template_markdown="圖片如下\n\n![封面](image_cover)",
        variables={},
        images={"image_cover": str(image_path)},
        allowed_paths=[str(tmp_path)],
        base_dir=tmp_path,
    )

    assert "<p>圖片如下</p>" in html
    assert 'alt="封面"' in html
    assert "data:image/jpeg;base64," in html


def test_ensure_note_title_html_prepends_missing_title():
    html = _ensure_note_title_html(
        "<p>來源：https://x.com/example</p><h2>簡介</h2><p>摘要</p>",
        "多目標追蹤模型",
    )

    assert html.startswith("<div><b>多目標追蹤模型</b></div>")


def test_ensure_note_title_html_does_not_duplicate_existing_title():
    html = _ensure_note_title_html(
        "<h1>多目標追蹤模型</h1><p>來源：https://x.com/example</p>",
        "多目標追蹤模型",
    )

    assert html.count("多目標追蹤模型") == 1


def test_notes_create_template_keeps_title_as_first_visible_line(tmp_path: Path):
    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=5,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
    )
    bridge._resolve_note_folder = MagicMock(
        return_value={
            "ok": True,
            "folder_id": "folder-1",
            "folder_path": "iCloud/待讀",
        }
    )
    captured: dict[str, str] = {}

    def fake_run_applescript(script, *, env=None, utf8_files=None, **kwargs):
        captured["note_body"] = (utf8_files or {}).get("NOTE_BODY", "")
        return "note-1"

    bridge._run_applescript = fake_run_applescript  # type: ignore[method-assign]
    bridge.notes_get = MagicMock(return_value={"ok": True, "note": {"id": "note-1"}})

    payload = bridge.notes_create(
        folder_id=None,
        folder_path="iCloud/待讀",
        title="多目標追蹤模型",
        body=None,
        template_markdown="來源：{url}\n\n## 簡介\n{summary}",
        variables={
            "url": "https://x.com/example",
            "summary": "這是一篇摘要",
        },
        images={},
    )

    assert payload["ok"] is True
    assert captured["note_body"].startswith("<div><b>多目標追蹤模型</b></div>")


def test_notes_get_renders_markdown_and_embedded_image_summary(tmp_path: Path):
    class FakeVisionAgent:
        def describe(self, image_parts):
            assert image_parts[0].text
            assert image_parts[1].data
            return "講座海報，時間是下週三晚上七點"

    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=5,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
        vision_agent=FakeVisionAgent(),
    )
    image_data = base64.b64encode(b"fake-image-bytes").decode("ascii")
    bridge._notes_get_raw = MagicMock(
        return_value={
            "ok": True,
            "note": {
                "id": "note-1",
                "title": "講座筆記",
                "body_html": (
                    f'<div><a href="https://example.com/post">原文</a></div>'
                    f'<div><img src="data:image/png;base64,{image_data}"></div>'
                    "<div>這是一段說明</div>"
                ),
                "plaintext": "這是一段說明",
                "created_at": "2026-04-11T10:00:00Z",
                "modified_at": "2026-04-11T12:00:00Z",
                "shared": False,
                "password_protected": False,
                "account": "iCloud",
                "folder_id": "folder-1",
                "folder_path": "iCloud/待讀",
            },
        }
    )

    payload = bridge.notes_get(note_id="note-1")

    assert payload["ok"] is True
    assert "data:image/png;base64" not in payload["note"]["content_markdown"]
    assert "講座海報" in payload["note"]["content_markdown"]
    assert payload["note"]["has_images"] is True
    assert payload["note"]["source_url"] == "https://example.com/post"
    assert payload["note"]["content_kind"] == "web_clip_image"
    assert (tmp_path / "cache" / "apple_notes").is_dir()


def test_notes_search_uses_cached_markdown_summary_and_paging(tmp_path: Path):
    class FakeSummarizer:
        def chat(self, messages, response_schema=None, temperature=None):
            content = messages[1].content
            assert isinstance(content, str)
            first_line = next((line for line in content.splitlines() if line.startswith("標題：")), "")
            return f"摘要 {first_line.removeprefix('標題：')}".strip()

    bridge = MacOSAppBridge(
        base_dir=tmp_path,
        allowed_paths=[str(tmp_path)],
        timeout_seconds=5,
        max_search_results=10,
        photos_export_dir="tmp/photos-exports",
        notes_summarizer=FakeSummarizer(),
    )
    bridge._notes_list_candidates = MagicMock(
        return_value={
            "ok": True,
            "results": [
                {
                    "id": "note-1",
                    "title": "講座 A",
                    "created_at": "2026-04-11T10:00:00Z",
                    "modified_at": "2026-04-11T12:00:00Z",
                    "shared": False,
                    "password_protected": False,
                    "account": "iCloud",
                    "folder_id": "folder-1",
                    "folder_path": "iCloud/待讀",
                },
                {
                    "id": "note-2",
                    "title": "講座 B",
                    "created_at": "2026-04-11T11:00:00Z",
                    "modified_at": "2026-04-11T13:00:00Z",
                    "shared": False,
                    "password_protected": False,
                    "account": "iCloud",
                    "folder_id": "folder-1",
                    "folder_path": "iCloud/待讀",
                },
                {
                    "id": "note-3",
                    "title": "別的文章",
                    "created_at": "2026-04-11T09:00:00Z",
                    "modified_at": "2026-04-11T09:30:00Z",
                    "shared": False,
                    "password_protected": False,
                    "account": "iCloud",
                    "folder_id": "folder-1",
                    "folder_path": "iCloud/待讀",
                },
            ],
        }
    )
    raw_notes = {
        "note-1": {
            "ok": True,
            "note": {
                "id": "note-1",
                "title": "講座 A",
                "body_html": "<div>下週三講座，主講人小明</div>",
                "plaintext": "下週三講座，主講人小明",
                "created_at": "2026-04-11T10:00:00Z",
                "modified_at": "2026-04-11T12:00:00Z",
                "shared": False,
                "password_protected": False,
                "account": "iCloud",
                "folder_id": "folder-1",
                "folder_path": "iCloud/待讀",
            },
        },
        "note-2": {
            "ok": True,
            "note": {
                "id": "note-2",
                "title": "講座 B",
                "body_html": "<div>今天的講座重點整理</div>",
                "plaintext": "今天的講座重點整理",
                "created_at": "2026-04-11T11:00:00Z",
                "modified_at": "2026-04-11T13:00:00Z",
                "shared": False,
                "password_protected": False,
                "account": "iCloud",
                "folder_id": "folder-1",
                "folder_path": "iCloud/待讀",
            },
        },
        "note-3": {
            "ok": True,
            "note": {
                "id": "note-3",
                "title": "別的文章",
                "body_html": "<div>這是一篇軟體更新文章</div>",
                "plaintext": "這是一篇軟體更新文章",
                "created_at": "2026-04-11T09:00:00Z",
                "modified_at": "2026-04-11T09:30:00Z",
                "shared": False,
                "password_protected": False,
                "account": "iCloud",
                "folder_id": "folder-1",
                "folder_path": "iCloud/待讀",
            },
        },
    }
    bridge._notes_get_raw = MagicMock(side_effect=lambda note_id: raw_notes[note_id])

    payload = bridge.notes_search(
        account=None,
        folder_id=None,
        folder_path="iCloud/待讀",
        query="講座",
        created_after=None,
        created_before=None,
        modified_after=None,
        modified_before=None,
        sort_by="modified_desc",
        limit=1,
        offset=1,
    )

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["total_matches"] == 2
    assert payload["has_more"] is False
    assert payload["results"][0]["id"] == "note-1"
    assert payload["results"][0]["summary"].startswith("摘要")
