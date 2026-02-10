"""Tests for reviewer retry helpers in CLI app."""

import json
from pathlib import Path

import pytest

from chat_agent.cli.app import (
    _TurnMemorySnapshot,
    _build_retry_directive,
    _build_reviewer_warning,
    _has_memory_write,
    _ensure_turn_persistence_action,
    _collect_required_actions_for_retry,
    _run_responder,
    _resolve_final_content,
    _sanitize_error_message,
    setup_tools,
)
from chat_agent.cli.console import ChatConsole
from chat_agent.context import ContextBuilder, Conversation
from chat_agent.core.schema import ToolsConfig
from chat_agent.llm.schema import LLMResponse, Message, ToolCall, ToolDefinition
from chat_agent.memory.editor.schema import AppliedItem, MemoryEditResult
from chat_agent.reviewer import RequiredAction
from chat_agent.reviewer.enforcement import (
    find_missing_actions,
    has_memory_write_to_any,
    build_label_enforcement_actions,
)
from chat_agent.reviewer.schema import LabelSignal
from chat_agent.tools import ToolRegistry


def test_build_retry_directive_contains_required_actions():
    directive = _build_retry_directive(
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

    assert "memory/short-term.md" in directive
    assert "write_or_edit" in directive
    assert "Complete actions before final answer." in directive
    assert "Execute now." in directive


def test_build_retry_directive_with_memory_edit_action():
    directive = _build_retry_directive(
        required_actions=[
            RequiredAction(
                code="persist_turn_memory",
                description="Persist rolling memory",
                tool="memory_edit",
                target_path="memory/short-term.md",
            )
        ],
    )

    assert "memory_edit" in directive
    assert "memory/short-term.md" in directive
    assert "Execute now." in directive


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

    missing = find_missing_actions(turn_messages, actions)
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

    missing = find_missing_actions(turn_messages, actions)
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

    missing = find_missing_actions(turn_messages, actions)
    assert missing == []


def test_build_reviewer_warning_for_model_error():
    warning = _build_reviewer_warning("Pre-review", None)
    assert "Pre-review" in warning
    assert "model call error" in warning


def test_build_reviewer_warning_includes_error_detail():
    warning = _build_reviewer_warning(
        "Post-review",
        None,
        "HTTP 404 from https://openrouter.ai/api/v1/chat/completions: "
        "No endpoints found matching your data policy (code=404)",
    )
    assert "Post-review" in warning
    assert "reason:" in warning
    assert "HTTP 404" in warning
    assert "data policy" in warning


def test_build_reviewer_warning_sanitizes_error_detail():
    warning = _build_reviewer_warning(
        "Post-review",
        None,
        "Bad url: https://example.com/path?api_key=secret-token",
    )
    assert "api_key=***" in warning
    assert "secret-token" not in warning


def test_build_reviewer_warning_for_invalid_output():
    warning = _build_reviewer_warning("Post-review", "not json")
    assert "Post-review" in warning
    assert "invalid JSON/schema" in warning


def test_sanitize_error_message_redacts_api_key():
    raw = (
        "Client error '429 Too Many Requests' for url "
        "'https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:"
        "generateContent?key=REDACTED_GOOGLE_API_KEY'"
    )
    sanitized = _sanitize_error_message(raw)
    assert "AIza" not in sanitized
    assert "key=***" in sanitized


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


def test_collect_required_actions_for_retry_when_passed_ignores_required_actions():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[],
        )
    ]
    required = [
        RequiredAction(
            code="update_short_term",
            description="Update short-term memory",
            tool="memory_edit",
            target_path="memory/short-term.md",
        )
    ]

    actions = _collect_required_actions_for_retry(
        turn_messages,
        passed=True,
        required_actions=required,
    )
    assert actions == []


def test_collect_required_actions_for_retry_when_passed_and_satisfied():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-09T15:31:00+08:00",
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
    required = [
        RequiredAction(
            code="update_short_term",
            description="Update short-term memory",
            tool="memory_edit",
            target_path="memory/short-term.md",
        )
    ]

    actions = _collect_required_actions_for_retry(
        turn_messages,
        passed=True,
        required_actions=required,
    )
    assert actions == []


def test_has_memory_write_to_any_exact_path():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
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
    assert has_memory_write_to_any(turn_messages, ("memory/short-term.md",)) is True
    assert has_memory_write_to_any(turn_messages, ("memory/agent/inner-state.md",)) is False


def test_has_memory_write_to_any_prefix_match():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/skills/git-awareness.md",
                                "payload_text": "new skill",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    assert has_memory_write_to_any(turn_messages, ("memory/agent/skills/",)) is True
    assert has_memory_write_to_any(turn_messages, ("memory/agent/interests/",)) is False


def test_build_label_enforcement_identity_change():
    """identity_change label without persona write triggers enforcement."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/experiences/rebirth.md",
                                "payload_text": "Identity milestone.",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    signals = [
        LabelSignal(label="identity_change", confidence=0.90),
    ]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert len(actions) == 1
    assert actions[0].code == "sync_identity_persona"
    assert actions[0].target_path == "memory/agent/persona.md"


def test_build_label_enforcement_skips_when_path_written():
    """identity_change label with persona write does not trigger enforcement."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/persona.md",
                                "payload_text": "Updated persona.",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    signals = [
        LabelSignal(label="identity_change", confidence=0.90),
    ]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert actions == []


def test_build_label_enforcement_low_confidence_skipped():
    """Labels below threshold are not enforced."""
    turn_messages = [Message(role="assistant", content="", tool_calls=[])]
    signals = [
        LabelSignal(label="skill_change", confidence=0.50),
    ]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert actions == []


def test_build_label_enforcement_skill_change():
    """skill_change label without skills write triggers enforcement."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
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
    signals = [
        LabelSignal(label="skill_change", confidence=0.85),
    ]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert len(actions) == 1
    assert actions[0].code == "persist_skill_change"
    assert actions[0].target_path_glob == "memory/agent/skills/*.md"


def test_build_label_enforcement_multiple_labels():
    """Multiple high-confidence labels each produce an action if unmet."""
    turn_messages = [Message(role="assistant", content="", tool_calls=[])]
    signals = [
        LabelSignal(label="rolling_context", confidence=0.90),
        LabelSignal(label="skill_change", confidence=0.85),
        LabelSignal(label="agent_state_shift", confidence=0.80),
    ]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    codes = {a.code for a in actions}
    assert "persist_rolling_context" in codes
    assert "persist_skill_change" in codes
    assert "persist_agent_state_shift" in codes


def test_build_label_enforcement_durable_user_fact_via_knowledge():
    """durable_user_fact is satisfied by writing to knowledge/."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/knowledge/health.md",
                                "payload_text": "fact",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    signals = [LabelSignal(label="durable_user_fact", confidence=0.90)]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert actions == []


def test_build_label_enforcement_durable_user_fact_via_people():
    """durable_user_fact is satisfied by writing to people/."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-10T21:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/people/user-yufeng.md",
                                "payload_text": "preference",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    signals = [LabelSignal(label="durable_user_fact", confidence=0.90)]
    actions = build_label_enforcement_actions(signals, turn_messages, threshold=0.75)
    assert actions == []


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


def test_resolve_final_content_ignores_tool_call_draft_message():
    content, used_fallback = _resolve_final_content(
        None,
        [
            Message(
                role="assistant",
                content="partial draft",
                tool_calls=[ToolCall(id="t1", name="noop", arguments={})],
            )
        ],
    )
    assert content == ""
    assert used_fallback is False


def test_turn_memory_snapshot_rolls_back_files(tmp_path: Path):
    working_dir = tmp_path
    short_term = working_dir / "memory" / "short-term.md"
    short_term.parent.mkdir(parents=True, exist_ok=True)
    short_term.write_text("before", encoding="utf-8")

    snapshot = _TurnMemorySnapshot(working_dir=working_dir)
    tool_call = ToolCall(
        id="m1",
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T23:44:00+08:00",
            "turn_id": "turn-rollback",
            "requests": [
                {
                    "request_id": "r1",
                    "kind": "append_entry",
                    "target_path": "memory/short-term.md",
                    "payload_text": "new line",
                },
                {
                    "request_id": "r2",
                    "kind": "create_if_missing",
                    "target_path": "memory/agent/thoughts/new.md",
                    "payload_text": "# temp",
                },
            ],
        },
    )

    snapshot.capture_from_tool_call(tool_call)

    short_term.write_text("after", encoding="utf-8")
    new_file = working_dir / "memory" / "agent" / "thoughts" / "new.md"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("# temp", encoding="utf-8")

    restored = snapshot.rollback()
    assert restored == 2
    assert short_term.read_text(encoding="utf-8") == "before"
    assert not new_file.exists()


class _ResponderSequenceClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat_with_tools_calls = 0

    def chat_with_tools(self, messages, tools):  # noqa: ANN001
        self.chat_with_tools_calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", tool_calls=[])

    def chat(self, messages):  # noqa: ANN001
        return ""


def _memory_edit_failed_result() -> str:
    return (
        '{"status":"failed","turn_id":"turn-x","applied":[],"errors":'
        '[{"request_id":"r1","code":"apply_failed","detail":"x"}]}'
    )


def _memory_edit_ok_result() -> str:
    return (
        '{"status":"ok","turn_id":"turn-x","applied":[{"request_id":"r1","status":"applied",'
        '"path":"memory/short-term.md"}],"errors":[]}'
    )


def _memory_edit_tool_call(tool_id: str) -> ToolCall:
    return ToolCall(
        id=tool_id,
        name="memory_edit",
        arguments={
            "as_of": "2026-02-09T17:00:00+08:00",
            "turn_id": "turn-x",
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


def test_run_responder_retries_memory_edit_failure_then_recovers():
    client = _ResponderSequenceClient(
        [
            LLMResponse(content=None, tool_calls=[_memory_edit_tool_call("tc1")]),
            LLMResponse(content=None, tool_calls=[_memory_edit_tool_call("tc2")]),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    tool_results = iter([_memory_edit_failed_result(), _memory_edit_ok_result()])

    registry = ToolRegistry()
    registry.register(
        "memory_edit",
        lambda **kwargs: next(tool_results),  # noqa: ARG005
        ToolDefinition(name="memory_edit", description="memory", parameters={}, required=[]),
    )

    console = ChatConsole(debug=False)
    response = _run_responder(
        client,
        builder.build(conversation),
        registry.get_definitions(),
        conversation,
        builder,
        registry,
        console,
    )

    assert response.content == "done"
    assert client.chat_with_tools_calls == 3


def test_run_responder_fails_closed_after_three_memory_edit_failures():
    client = _ResponderSequenceClient(
        [
            LLMResponse(content=None, tool_calls=[_memory_edit_tool_call("tc1")]),
            LLMResponse(content=None, tool_calls=[_memory_edit_tool_call("tc2")]),
            LLMResponse(content=None, tool_calls=[_memory_edit_tool_call("tc3")]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "memory_edit",
        lambda **kwargs: _memory_edit_failed_result(),  # noqa: ARG005
        ToolDefinition(name="memory_edit", description="memory", parameters={}, required=[]),
    )

    console = ChatConsole(debug=False)
    with pytest.raises(RuntimeError, match="failed 3 times"):
        _run_responder(
            client,
            builder.build(conversation),
            registry.get_definitions(),
            conversation,
            builder,
            registry,
            console,
        )


class _DummyMemoryEditor:
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
        )


def test_setup_tools_blocks_memory_write_file(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=_DummyMemoryEditor(),
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
        memory_editor=_DummyMemoryEditor(),
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
        memory_editor=_DummyMemoryEditor(),
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
        memory_editor=_DummyMemoryEditor(),
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
        memory_editor=_DummyMemoryEditor(),
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
        memory_editor=_DummyMemoryEditor(),
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
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
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
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
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
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
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
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
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


def test_memory_edit_auto_fills_target_path_from_index_path(tmp_path: Path):
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m5",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-09T10:46:00+08:00",
                "turn_id": "turn-5",
                "requests": [
                    {
                        "request_id": "r1",
                        "kind": "ensure_index_link",
                        "index_path": "memory/agent/thoughts/index.md",
                        "link_path": "memory/agent/thoughts/2026-02-09-calculation-error.md",
                        "link_title": "計算修正",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    request = writer.last_batch.requests[0]
    assert request.kind == "ensure_index_link"
    assert request.target_path == "memory/agent/thoughts/index.md"
    assert request.index_path == "memory/agent/thoughts/index.md"


def test_memory_edit_maps_old_string_new_string_to_replace_block(tmp_path: Path):
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m6",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-09T16:30:00+08:00",
                "turn_id": "turn-6",
                "requests": [
                    {
                        "request_id": "r1",
                        "path": "memory/agent/persona.md",
                        "old_string": "# Persona: 卉 (HUI)",
                        "new_string": "# Persona: 澪希 (LING-XI)",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    request = writer.last_batch.requests[0]
    assert request.kind == "replace_block"
    assert request.target_path == "memory/agent/persona.md"
    assert request.old_block == "# Persona: 卉 (HUI)"
    assert request.new_block == "# Persona: 澪希 (LING-XI)"


def test_memory_edit_infers_toggle_checkbox_from_payload_line(tmp_path: Path):
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m7",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-09T16:40:00+08:00",
                "turn_id": "turn-7",
                "requests": [
                    {
                        "request_id": "r1",
                        "kind": "toggle_checkbox",
                        "target_path": "memory/agent/pending-thoughts.md",
                        "payload_text": "- [x] 起床追蹤",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    request = writer.last_batch.requests[0]
    assert request.kind == "toggle_checkbox"
    assert request.item_text == "起床追蹤"
    assert request.checked is True


def test_memory_edit_degrades_invalid_toggle_to_append_when_payload_exists(tmp_path: Path):
    writer = _DummyMemoryEditor()
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=writer,
    )

    result = registry.execute(
        ToolCall(
            id="m8",
            name="memory_edit",
            arguments={
                "as_of": "2026-02-09T16:41:00+08:00",
                "turn_id": "turn-8",
                "requests": [
                    {
                        "request_id": "r1",
                        "kind": "toggle_checkbox",
                        "target_path": "memory/agent/pending-thoughts.md",
                        "payload_text": "補一條待辦",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result
    assert writer.last_batch is not None
    request = writer.last_batch.requests[0]
    assert request.kind == "append_entry"
    assert request.payload_text == "補一條待辦"


def test_build_retry_directive_with_empty_reply_violation():
    directive = _build_retry_directive(
        retry_instruction="回覆為空，請提供有意義的回應。",
        required_actions=[],
        violations=["empty_reply"],
    )

    assert "empty_reply" in directive
    assert "Fix and re-answer." in directive
    assert "回覆為空" in directive


def test_build_retry_directive_violations_only():
    """violations-only path without retry_instruction still mentions the violation."""
    directive = _build_retry_directive(
        required_actions=[],
        violations=["empty_reply"],
    )

    assert "empty_reply" in directive
    assert "Fix and re-answer." in directive
