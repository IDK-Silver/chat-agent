"""Tests for reviewer retry helpers in CLI app."""

from chat_agent.cli.app import (
    _build_retry_reminder,
    _find_missing_actions,
    _build_reviewer_warning,
    _has_memory_write,
    _ensure_turn_persistence_action,
)
from chat_agent.llm.schema import Message, ToolCall
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
                    name="edit_file",
                    arguments={"path": "memory/short-term.md", "old_string": "a", "new_string": "b"},
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
