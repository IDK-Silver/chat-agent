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
                    "target_path": "memory/agent/short-term.md",
                    "instruction": "append short-term entry",
                },
                {
                    "request_id": "r2",
                    "target_path": "memory/agent/inner-state.md",
                    "instruction": "append state entry",
                },
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text.startswith("MemoryEdit: 2 request(s)")
    assert "\n  - memory/agent/short-term.md" in text
    assert "\n  - memory/agent/inner-state.md" in text
    assert "memory/agent/short-term.md" in text
    assert "memory/agent/inner-state.md" in text


def test_format_tool_call_memory_edit_ignores_updates_alias():
    tool_call = ToolCall(
        id="m2",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T01:10:00+08:00",
            "turn_id": "turn-2",
            "updates": [
                {
                    "request_id": "r1",
                    "target_path": "memory/agent/short-term.md",
                    "instruction": "append entry",
                }
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text == "MemoryEdit: 0 request(s)"


def test_format_tool_call_memory_edit_requires_target_path_key():
    tool_call = ToolCall(
        id="m2b",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T01:12:00+08:00",
            "turn_id": "turn-2b",
            "requests": [
                {
                    "request_id": "r1",
                    "targetPath": "memory/agent/short-term.md",
                    "instruction": "append entry",
                }
            ],
        },
    )

    text = format_tool_call(tool_call)
    assert text.startswith("MemoryEdit: 1 request(s)")
    assert "memory/agent/short-term.md" not in text


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
                    "path": "memory/agent/short-term.md",
                },
                {
                    "request_id": "r2",
                    "status": "noop",
                    "path": "memory/agent/inner-state.md",
                },
            ],
            "errors": [],
        },
        ensure_ascii=False,
    )

    text = format_tool_result(tool_call, result)
    assert "status=ok" in text
    assert "\nfiles:\n" in text
    assert "\n  - memory/agent/short-term.md(applied)" in text
    assert "\n  - memory/agent/inner-state.md(noop)" in text
    assert "memory/agent/short-term.md(applied)" in text
    assert "memory/agent/inner-state.md(noop)" in text


def test_format_tool_result_memory_edit_ignores_legacy_result_fields():
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
                    "target_path": "memory/agent/short-term.md",
                }
            ],
            "errors": [],
        },
        ensure_ascii=False,
    )

    text = format_tool_result(tool_call, result)
    assert "files=" not in text
