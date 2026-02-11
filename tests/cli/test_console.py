"""Tests for ChatConsole tool trace behavior."""

from __future__ import annotations

import json

from rich.console import Console

from chat_agent.cli.console import ChatConsole
from chat_agent.llm.schema import ToolCall


def _make_console(*, debug: bool = False, show_tool_use: bool = False) -> ChatConsole:
    console = ChatConsole(debug=debug, show_tool_use=show_tool_use)
    console.console = Console(record=True, force_terminal=False, color_system=None, width=200)
    return console


def test_non_debug_hides_normal_tool_traces():
    console = _make_console(debug=False)
    tool_call = ToolCall(
        id="1",
        name="read_file",
        arguments={"path": "memory/short-term.md"},
    )

    console.print_tool_call(tool_call)
    console.print_tool_result(tool_call, "line-1\nline-2")

    assert console.console.export_text().strip() == ""


def test_non_debug_shows_warning_on_error_result():
    console = _make_console(debug=False)
    tool_call = ToolCall(
        id="2",
        name="write_file",
        arguments={"path": "notes/demo.md", "content": "x"},
    )

    console.print_tool_result(tool_call, "Error: write failed")

    text = console.console.export_text()
    assert "Warning:" in text
    assert "write_file failed" in text


def test_non_debug_shows_warning_on_failed_json_result():
    console = _make_console(debug=False)
    tool_call = ToolCall(
        id="3",
        name="memory_edit",
        arguments={},
    )
    result = json.dumps(
        {
            "status": "failed",
            "turn_id": "turn-3",
            "applied": [],
            "errors": [{"request_id": "r1", "code": "apply_failed", "detail": "x"}],
        }
    )

    console.print_tool_result(tool_call, result)

    text = console.console.export_text()
    assert "Warning:" in text
    assert "memory_edit failed" in text


def test_print_warning_supports_indent():
    console = _make_console(debug=False)
    console.print_warning("indented warning", indent=2)
    text = console.console.export_text()
    assert "\n  Warning: indented warning\n" in f"\n{text}\n"


def test_debug_shows_tool_traces():
    console = _make_console(show_tool_use=True)
    tool_call = ToolCall(
        id="4",
        name="write_file",
        arguments={"path": "notes/demo.md", "content": "x"},
    )

    console.print_tool_call(tool_call)
    console.print_tool_result(tool_call, "Successfully wrote 1 bytes to notes/demo.md")

    text = console.console.export_text()
    assert "Write: notes/demo.md" in text
    assert "Successfully wrote 1 bytes to notes/demo.md" in text


def test_set_show_tool_use_toggles_visibility():
    console = _make_console(debug=False)
    tool_call = ToolCall(
        id="5",
        name="read_file",
        arguments={"path": "memory/agent/persona.md"},
    )

    console.print_tool_call(tool_call)
    assert console.console.export_text().strip() == ""

    console.set_show_tool_use(True)
    console.print_tool_call(tool_call)
    assert "Read: memory/agent/persona.md" in console.console.export_text()


def test_debug_block_keeps_literal_brackets():
    console = _make_console(debug=True, show_tool_use=True)
    console.print_debug_block("post-review", "[update_short_term] keep brackets")
    text = console.console.export_text()
    assert "[update_short_term] keep brackets" in text
