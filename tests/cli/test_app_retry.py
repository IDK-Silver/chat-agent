"""Tests for reviewer retry helpers in CLI app."""

import json
from pathlib import Path

from chat_agent.cli.app import (
    _build_retry_reminder,
    _find_missing_actions,
    _build_reviewer_warning,
    _has_memory_write,
    _ensure_turn_persistence_action,
    _resolve_final_content,
    setup_tools,
)
from chat_agent.core.schema import ToolsConfig
from chat_agent.llm.schema import Message, ToolCall
from chat_agent.memory_writer.schema import AppliedItem, MemoryEditResult
from chat_agent.reviewer import RequiredAction


def test_build_retry_reminder_contains_required_actions():
    reminder = _build_retry_reminder(
        retry_instruction="Complete actions before final answer.",
        required_actions=[
            RequiredAction(
                code="update_short_term",
                description="Update short-term summary for new topic",
                tool="write_or_edit",
                target_path="memory/short-term.md",
            )
        ],
    )

    assert "COMPLIANCE RETRY" in reminder
    assert "update_short_term" in reminder
    assert "memory/short-term.md" in reminder
    assert "Complete actions before final answer." in reminder


def test_build_retry_reminder_includes_memory_edit_payload_template():
    reminder = _build_retry_reminder(
        retry_instruction="",
        required_actions=[
            RequiredAction(
                code="persist_turn_memory",
                description="Persist rolling memory",
                tool="memory_edit",
                target_path="memory/short-term.md",
            )
        ],
    )

    assert "memory_edit minimal payload" in reminder
    assert '"as_of"' in reminder
    assert '"turn_id"' in reminder
    assert '"requests"' in reminder
    assert '"request_id"' in reminder
    assert '"kind"' in reminder


def test_find_missing_actions_when_satisfied():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="write_file",
                    arguments={"path": "memory/agent/knowledge/health.md", "content": "..."},
                ),
                ToolCall(
                    id="2",
                    name="edit_file",
                    arguments={"path": "memory/agent/knowledge/index.md", "old_string": "x", "new_string": "y"},
                ),
            ],
        )
    ]
    actions = [
        RequiredAction(
            code="write_knowledge",
            description="Persist durable fact to knowledge",
            tool="write_or_edit",
            target_path_glob="memory/agent/knowledge/*.md",
            index_path="memory/agent/knowledge/index.md",
        )
    ]

    missing = _find_missing_actions(turn_messages, actions)
    assert missing == []


def test_find_missing_actions_when_index_not_updated():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="write_file",
                    arguments={"path": "memory/agent/knowledge/health.md", "content": "..."},
                )
            ],
        )
    ]
    actions = [
        RequiredAction(
            code="write_knowledge",
            description="Persist durable fact to knowledge",
            tool="write_or_edit",
            target_path_glob="memory/agent/knowledge/*.md",
            index_path="memory/agent/knowledge/index.md",
        )
    ]

    missing = _find_missing_actions(turn_messages, actions)
    assert len(missing) == 1
    assert missing[0].code == "write_knowledge"


def test_find_missing_actions_satisfied_by_memory_edit_with_index_update():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-08T22:30:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/knowledge/health.md",
                                "payload_text": "entry",
                            },
                            {
                                "request_id": "r2",
                                "kind": "ensure_index_link",
                                "target_path": "memory/agent/knowledge/index.md",
                                "index_path": "memory/agent/knowledge/index.md",
                                "link_path": "memory/agent/knowledge/health.md",
                                "link_title": "Health",
                            },
                        ],
                    },
                )
            ],
        )
    ]
    actions = [
        RequiredAction(
            code="write_knowledge",
            description="Persist durable fact to knowledge",
            tool="memory_edit",
            target_path_glob="memory/agent/knowledge/*.md",
            index_path="memory/agent/knowledge/index.md",
        )
    ]

    missing = _find_missing_actions(turn_messages, actions)
    assert missing == []


def test_build_reviewer_warning_for_model_error():
    warning = _build_reviewer_warning("Pre-review", None)
    assert "Pre-review" in warning
    assert "model call error" in warning


def test_build_reviewer_warning_for_invalid_output():
    warning = _build_reviewer_warning("Post-review", "not json")
    assert "Post-review" in warning
    assert "invalid JSON/schema" in warning


def test_has_memory_write_true_for_memory_edit():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-08T22:30:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    assert _has_memory_write(turn_messages) is True


def test_has_memory_write_false_for_non_memory_write():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="write_file",
                    arguments={"path": "notes/tmp.md", "content": "x"},
                )
            ],
        )
    ]
    assert _has_memory_write(turn_messages) is False


def test_ensure_turn_persistence_action_adds_when_missing():
    actions = [
        RequiredAction(
            code="check_time",
            description="Call time tool first",
            tool="get_current_time",
        )
    ]

    merged = _ensure_turn_persistence_action(actions)
    assert len(merged) == 2
    assert any(a.code == "persist_turn_memory" for a in merged)


def test_ensure_turn_persistence_action_keeps_existing_memory_write_action():
    actions = [
        RequiredAction(
            code="update_short_term",
            description="Update short-term",
            tool="write_or_edit",
            target_path="memory/short-term.md",
        )
    ]

    merged = _ensure_turn_persistence_action(actions)
    assert merged == actions


def test_resolve_final_content_uses_response_when_present():
    content, used_fallback = _resolve_final_content(
        "final answer",
        [
            Message(role="assistant", content="from tool call"),
        ],
    )
    assert content == "final answer"
    assert used_fallback is False


def test_resolve_final_content_falls_back_to_latest_assistant_message():
    content, used_fallback = _resolve_final_content(
        "",
        [
            Message(role="assistant", content="first text"),
            Message(role="tool", content="ok"),
            Message(role="assistant", content="second text"),
        ],
    )
    assert content == "second text"
    assert used_fallback is True


def test_resolve_final_content_returns_empty_when_no_assistant_text():
    content, used_fallback = _resolve_final_content(
        None,
        [
            Message(role="assistant", content="   "),
            Message(role="tool", content="ok"),
        ],
    )
    assert content == ""
    assert used_fallback is False


class _DummyMemoryWriter:
    def __init__(self):
        self.last_batch = None

    def apply_batch(self, batch, *, allowed_paths, base_dir):  # noqa: ANN001
        self.last_batch = batch
        return MemoryEditResult(
            status="ok",
            turn_id=batch.turn_id,
            applied=[
                AppliedItem(
                    request_id=r.request_id,
                    status="applied",
                    path=r.target_path,
                )
                for r in batch.requests
            ],
            errors=[],
            writer_attempts={r.request_id: 1 for r in batch.requests},
        )


def test_setup_tools_blocks_memory_write_file(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="w1",
            name="write_file",
            arguments={"path": "memory/short-term.md", "content": "x"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_edit_file(tmp_path: Path):
    target = tmp_path / "memory" / "short-term.md"
    target.parent.mkdir(parents=True)
    target.write_text("old")

    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="e1",
            name="edit_file",
            arguments={"path": "memory/short-term.md", "old_string": "old", "new_string": "new"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result
    assert target.read_text() == "old"


def test_setup_tools_blocks_memory_shell_write(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="s1",
            name="execute_shell",
            arguments={"command": "printf 'x' > memory/short-term.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_shell_write_without_space(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="s1b",
            name="execute_shell",
            arguments={"command": "echo x>>memory/short-term.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_shell_write_via_tee(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="s2",
            name="execute_shell",
            arguments={"command": "echo hi | tee memory/short-term.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_shell_write_via_sed_i(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=_DummyMemoryWriter(),
    )
    result = registry.execute(
        ToolCall(
            id="s3",
            name="execute_shell",
            arguments={"command": "sed -i 's/a/b/' memory/short-term.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_registers_memory_edit(tmp_path: Path):
    writer = _DummyMemoryWriter()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=writer,
    )
    assert registry.has_tool("memory_edit") is True

    result = registry.execute(
        ToolCall(
            id="m1",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-08T22:30:00+08:00",
                "turn_id": "turn-1",
                "requests": [
                    {
                        "request_id": "r1",
                        "kind": "create_if_missing",
                        "target_path": "memory/agent/skills/demo.md",
                        "payload_text": "hello",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result


def test_memory_edit_accepts_compat_alias_fields(tmp_path: Path):
    writer = _DummyMemoryWriter()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m2",
            name="memory_edit",
            arguments={
                "timestamp": "2026-02-08T22:31:00+08:00",
                "turn": "turn-2",
                "updates": [
                    {
                        "id": "r-compat",
                        "action": "append_entry",
                        "path": "memory/short-term.md",
                        "content": "compat payload",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    assert writer.last_batch.as_of == "2026-02-08T22:31:00+08:00"
    assert writer.last_batch.turn_id == "turn-2"
    request = writer.last_batch.requests[0]
    assert request.request_id == "r-compat"
    assert request.kind == "append_entry"
    assert request.target_path == "memory/short-term.md"
    assert request.payload_text == "compat payload"


def test_memory_edit_accepts_json_string_requests(tmp_path: Path):
    writer = _DummyMemoryWriter()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m3",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-08T22:32:00+08:00",
                "turn_id": "turn-3",
                "requests": json.dumps(
                    [
                        {
                            "request_id": "r-json",
                            "kind": "create_if_missing",
                            "target_path": "memory/agent/skills/demo.md",
                            "payload_text": "hello",
                        }
                    ]
                ),
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    assert writer.last_batch.requests[0].request_id == "r-json"


def test_memory_edit_auto_fills_missing_request_id_and_kind(tmp_path: Path):
    writer = _DummyMemoryWriter()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_writer=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m4",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-09T01:08:00+08:00",
                "turn_id": "turn-4",
                "requests": [
                    {
                        "path": "memory/short-term.md",
                        "content": "- [2026-02-09 01:08] test",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    request = writer.last_batch.requests[0]
    assert request.request_id == "auto-1"
    assert request.kind == "append_entry"
    assert request.target_path == "memory/short-term.md"
    assert request.payload_text == "- [2026-02-09 01:08] test"
