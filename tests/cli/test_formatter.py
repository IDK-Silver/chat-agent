"""Tests for CLI formatter helpers."""

import json

from chat_agent.cli.formatter import format_tool_call, format_tool_result
from chat_agent.llm.schema import ToolCall


def test_format_tool_call_memory_edit_shows_target_paths():
    tool_call = ToolCall(
        id="m1",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T01:10:00+08:00",
            "turn_id": "turn-1",
            "requests": [
                {
                    "request_id": "r1",
                    "kind": "append_entry",
                    "target_path": "memory/short-term.md",
                    "payload_text": "a",
                },
                {
                    "request_id": "r2",
                    "kind": "append_entry",
                    "target_path": "memory/agent/inner-state.md",
                    "payload_text": "b",
                },
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text.startswith("MemoryEdit: 2 request(s)")
    assert "memory/short-term.md" in text
    assert "memory/agent/inner-state.md" in text


def test_format_tool_call_memory_edit_supports_updates_alias():
    tool_call = ToolCall(
        id="m2",
        name="memory_edit",
        arguments={
            "timestamp": "2026-02-09T01:10:00+08:00",
            "turn": "turn-2",
            "updates": [
                {
                    "id": "r1",
                    "action": "append_entry",
                    "path": "memory/short-term.md",
                    "content": "x",
                }
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text.startswith("MemoryEdit: 1 request(s)")
    assert "memory/short-term.md" in text


def test_format_tool_call_memory_edit_supports_camel_case_paths():
    tool_call = ToolCall(
        id="m2b",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T01:12:00+08:00",
            "turn_id": "turn-2b",
            "requests": [
                {
                    "requestId": "r1",
                    "kind": "append_entry",
                    "targetPath": "memory/short-term.md",
                    "payloadText": "x",
                }
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text.startswith("MemoryEdit: 1 request(s)")
    assert "memory/short-term.md" in text


def test_format_tool_result_memory_edit_shows_file_statuses():
    tool_call = ToolCall(
        id="m3",
        name="memory_edit",
        arguments={},
    )
    result = json.dumps(
        {
            "status": "ok",
            "turn_id": "turn-3",
            "applied": [
                {
                    "request_id": "r1",
                    "status": "applied",
                    "path": "memory/short-term.md",
                },
                {
                    "request_id": "r2",
                    "status": "noop",
                    "path": "memory/agent/inner-state.md",
                },
            ],
            "errors": [],
            "writer_attempts": {"r1": 1, "r2": 1},
        },
        ensure_ascii=False,
    )

    text = format_tool_result(tool_call, result)
    assert "status=ok" in text
    assert "files=" in text
    assert "memory/short-term.md(applied)" in text
    assert "memory/agent/inner-state.md(noop)" in text


def test_format_tool_result_memory_edit_supports_target_path_field():
    tool_call = ToolCall(
        id="m4",
        name="memory_edit",
        arguments={},
    )
    result = json.dumps(
        {
            "status": "ok",
            "turn_id": "turn-4",
            "applied": [
                {
                    "request_id": "r1",
                    "apply_status": "applied",
                    "target_path": "memory/short-term.md",
                }
            ],
            "errors": [],
            "writer_attempts": {"r1": 1},
        },
        ensure_ascii=False,
    )

    text = format_tool_result(tool_call, result)
    assert "files=memory/short-term.md(applied)" in text
