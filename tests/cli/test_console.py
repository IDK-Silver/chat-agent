"""Tests for ChatConsole tool trace behavior."""

from __future__ import annotations

import json

from rich.console import Console

from chat_agent.cli.console import ChatConsole
from chat_agent.llm.schema import Message, ToolCall


def _make_console(*, debug: bool = False, show_tool_use: bool = False) -> ChatConsole:
    console = ChatConsole(debug=debug, show_tool_use=show_tool_use)
    console.console = Console(record=True, force_terminal=False, color_system=None, width=200)
    return console


def test_non_debug_hides_normal_tool_traces():
    console = _make_console(debug=False)
    tool_call = ToolCall(
        id="1",
        name="read_file",
        arguments={"path": "memory/agent/short-term.md"},
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


def test_print_assistant_no_truncation_on_narrow_terminal():
    """Long lines must wrap, not get silently cropped."""
    console = ChatConsole()
    console.console = Console(
        record=True, force_terminal=False, color_system=None, width=40
    )
    long_word = "A" * 80
    console.print_assistant(long_word)
    text = console.console.export_text()
    # Rich wraps long lines; join them back to verify nothing was lost
    joined = text.replace("\n", "").replace(" ", "")
    assert long_word in joined


# --- Resume display tests ---


def test_resume_shows_intermediate_text_from_tool_call_messages():
    """Assistant content from messages with tool_calls must be shown on resume."""
    console = _make_console()
    messages = [
        Message(role="user", content="hello"),
        Message(
            role="assistant",
            content="intermediate reply",
            tool_calls=[ToolCall(id="t1", name="get_time", arguments={})],
        ),
        Message(role="tool", content="12:00", tool_call_id="t1", name="get_time"),
    ]
    console.print_resume_history(messages, replay_turns=None, show_tool_calls=False)
    text = console.console.export_text()
    assert "intermediate reply" in text


def test_resume_shows_tool_calls_and_results():
    """With show_tool_calls=True, tool calls and results are shown."""
    console = _make_console()
    messages = [
        Message(role="user", content="check files"),
        Message(
            role="assistant",
            content="Let me check.",
            tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "test.md"})],
        ),
        Message(role="tool", content="file contents here\nline2", tool_call_id="t1", name="read_file"),
    ]
    console.print_resume_history(messages, replay_turns=None, show_tool_calls=True)
    text = console.console.export_text()
    # Assistant content shown
    assert "Let me check." in text
    # Tool call shown (format_tool_call produces "Read: test.md")
    assert "Read: test.md" in text
    # Tool result shown via format_tool_result
    assert "2 lines" in text


def test_resume_hides_tool_calls_but_shows_content():
    """With show_tool_calls=False, tool details are hidden but content is shown."""
    console = _make_console()
    messages = [
        Message(role="user", content="do something"),
        Message(
            role="assistant",
            content="Here is my response.",
            tool_calls=[ToolCall(id="t1", name="execute_shell", arguments={"command": "ls"})],
        ),
        Message(role="tool", content="file1\nfile2", tool_call_id="t1", name="execute_shell"),
    ]
    console.print_resume_history(messages, replay_turns=None, show_tool_calls=False)
    text = console.console.export_text()
    assert "Here is my response." in text
    # Tool call and result should NOT appear
    assert "Shell:" not in text
    assert "file1" not in text


def test_resume_failed_tool_result_styling():
    """Failed tool results should still be displayed on resume."""
    console = _make_console()
    messages = [
        Message(role="user", content="write something"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="t1", name="write_file", arguments={"path": "x.md", "content": "x"})],
        ),
        Message(role="tool", content="Error: permission denied", tool_call_id="t1", name="write_file"),
    ]
    console.print_resume_history(messages, replay_turns=None, show_tool_calls=True)
    text = console.console.export_text()
    assert "Error" in text
