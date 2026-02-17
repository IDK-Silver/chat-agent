"""Tests for reviewer retry helpers in CLI app."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from chat_agent.cli.app import (
    _TurnMemorySnapshot,
    _build_missing_visible_reply_directive,
    _build_post_review_packet_messages,
    _build_retry_directive,
    _promote_anomaly_targets_to_sticky,
    _resolve_effective_target_signals,
    _build_reviewer_warning,
    _format_anomaly_retry_instruction,
    _filter_retry_violations,
    _has_memory_write,
    _ensure_turn_persistence_action,
    _collect_required_actions_for_retry,
    _run_responder,
    _latest_intermediate_text,
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
from chat_agent.reviewer.progress_schema import ProgressReviewResult
from chat_agent.reviewer.enforcement import (
    _collect_memory_write_paths,
    _extract_applied_paths_from_result,
    build_target_enforcement_actions,
    detect_persistence_anomalies,
    find_missing_actions,
    has_memory_write_to_any,
    merge_anomaly_signals,
)
from chat_agent.reviewer.schema import AnomalySignal, TargetSignal
from chat_agent.tools import ToolRegistry


def test_build_post_review_packet_messages_scopes_to_latest_attempt():
    messages = [
        Message(role="user", content="old turn"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="new request"),
        Message(role="assistant", content="attempt1"),
        Message(role="tool", name="memory_edit", tool_call_id="m1", content='{"status":"ok"}'),
        Message(role="assistant", content="attempt2"),
        Message(role="tool", name="memory_edit", tool_call_id="m2", content='{"status":"ok"}'),
    ]

    packet_messages = _build_post_review_packet_messages(
        messages,
        turn_anchor=2,
        attempt_anchor=5,
    )

    assert [m.role for m in packet_messages] == ["user", "assistant", "user", "assistant", "tool"]
    assert packet_messages[2].content == "new request"
    assert packet_messages[3].content == "attempt2"
    assert packet_messages[4].tool_call_id == "m2"


def test_build_post_review_packet_messages_first_attempt_returns_full_turn():
    messages = [
        Message(role="user", content="old turn"),
        Message(role="assistant", content="old reply"),
        Message(role="user", content="new request"),
        Message(role="assistant", content="attempt1"),
    ]

    packet_messages = _build_post_review_packet_messages(
        messages,
        turn_anchor=2,
        attempt_anchor=2,
    )

    assert packet_messages == messages


def test_filter_retry_violations_drops_stale_turn_not_persisted():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-11T02:20:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "append rolling context",
                            }
                        ],
                    },
                )
            ],
        )
    ]

    violations = _filter_retry_violations(
        ["turn_not_persisted", "near_time_context_missed"],
        turn_messages=turn_messages,
    )

    assert violations == ["near_time_context_missed"]


def test_filter_retry_violations_keeps_turn_not_persisted_without_memory_write():
    turn_messages = [Message(role="assistant", content="no tools")]

    violations = _filter_retry_violations(
        ["turn_not_persisted"],
        turn_messages=turn_messages,
    )

    assert violations == ["turn_not_persisted"]


def test_build_retry_directive_contains_required_actions():
    directive = _build_retry_directive(
        retry_instruction="Complete actions before final answer.",
        attempt=1,
        max_attempts=5,
        required_actions=[
            RequiredAction(
                code="update_short_term",
                description="Update short-term summary for new topic",
                tool="write_or_edit",
                target_path="memory/agent/short-term.md",
            )
        ],
    )

    assert "[RETRY CONTRACT]" in directive
    assert "Never call it yourself" in directive
    assert "attempt: 1/5" in directive
    assert "memory/agent/short-term.md" in directive
    assert "missing_targets:" in directive
    assert "write_or_edit" in directive
    assert "Complete actions before final answer." in directive
    assert "completion_criteria:" in directive
    assert "hard_rule:" in directive
    assert "Do NOT output user-facing reply before completion." in directive
    assert "Execute now." in directive


def test_build_retry_directive_with_memory_edit_action():
    directive = _build_retry_directive(
        required_actions=[
            RequiredAction(
                code="persist_turn_memory",
                description="Persist rolling memory",
                tool="memory_edit",
                target_path="memory/agent/short-term.md",
            )
        ],
    )

    assert "memory_edit" in directive
    assert "memory/agent/short-term.md" in directive
    assert "Execute now." in directive


def test_build_retry_directive_with_memory_edit_glob_action():
    directive = _build_retry_directive(
        required_actions=[
            RequiredAction(
                code="persist_user_fact",
                description="Persist durable user fact",
                tool="memory_edit",
                target_path_glob="memory/agent/knowledge/*.md",
                index_path="memory/agent/knowledge/index.md",
            )
        ],
    )

    assert "target_path_glob: memory/agent/knowledge/*.md" in directive
    assert "memory_search" in directive
    assert "NEVER use wildcard characters" in directive
    assert "<exact_path_not_glob>" in directive
    assert "if index update is required, write target_path to memory/agent/knowledge/index.md" in directive


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


def test_find_missing_actions_ignores_failed_memory_edit():
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-08T22:30:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "kind": "append_entry",
                                "target_path": "memory/agent/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m1",
            content=(
                '{"status":"failed","turn_id":"turn-1","applied":[],'
                '"errors":[{"request_id":"r1","code":"apply_failed"}]}'
            ),
        ),
    ]
    actions = [
        RequiredAction(
            code="update_short_term",
            description="Persist short-term",
            tool="memory_edit",
            target_path="memory/agent/short-term.md",
        )
    ]

    missing = find_missing_actions(turn_messages, actions)
    assert len(missing) == 1
    assert missing[0].code == "update_short_term"


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
        "generateContent?key=AIzaSyCloaQ8BkbpKJtIPeEB0ITTDXGFcQoIeAg'"
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
                                "target_path": "memory/agent/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        )
    ]
    assert _has_memory_write(turn_messages) is True


def test_has_memory_write_false_for_failed_memory_edit():
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
                                "target_path": "memory/agent/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="1",
            content=(
                '{"status":"failed","turn_id":"turn-1","applied":[],'
                '"errors":[{"request_id":"r1","code":"apply_failed"}]}'
            ),
        ),
    ]
    assert _has_memory_write(turn_messages) is False


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
            target_path="memory/agent/short-term.md",
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
            target_path="memory/agent/short-term.md",
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
                                "target_path": "memory/agent/short-term.md",
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
            target_path="memory/agent/short-term.md",
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
                                "target_path": "memory/agent/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/short-term.md"]),
    ]
    assert has_memory_write_to_any(turn_messages, ("memory/agent/short-term.md",)) is True
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
        ),
        _ok_tool_result("m1", ["memory/agent/skills/git-awareness.md"]),
    ]
    assert has_memory_write_to_any(turn_messages, ("memory/agent/skills/",)) is True
    assert has_memory_write_to_any(turn_messages, ("memory/agent/interests/",)) is False


def test_has_memory_write_to_any_ignores_failed_memory_edit():
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
                                "target_path": "memory/agent/short-term.md",
                                "payload_text": "entry",
                            }
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m1",
            content=(
                '{"status":"failed","turn_id":"turn-1","applied":[],'
                '"errors":[{"request_id":"r1","code":"apply_failed"}]}'
            ),
        ),
    ]
    assert has_memory_write_to_any(turn_messages, ("memory/agent/short-term.md",)) is False


def test_build_target_enforcement_identity_target():
    """persona target without persona write triggers enforcement action."""
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
                                "target_path": "memory/agent/experiences/rebirth.md",
                                "instruction": "記錄事件",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/experiences/rebirth.md"]),
    ]
    signals = [TargetSignal(signal="target_persona", requires_persistence=True)]
    actions = build_target_enforcement_actions(
        signals,
        turn_messages,
        current_user="yufeng",
    )
    assert len(actions) == 1
    assert actions[0].code == "persist_target_persona"
    assert actions[0].target_path == "memory/agent/persona.md"


def test_build_target_enforcement_skips_when_target_path_written():
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
                                "target_path": "memory/agent/persona.md",
                                "instruction": "更新 persona",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/persona.md"]),
    ]
    signals = [TargetSignal(signal="target_persona")]
    actions = build_target_enforcement_actions(
        signals,
        turn_messages,
        current_user="yufeng",
    )
    assert actions == []


def test_build_target_enforcement_requires_index_for_folder_targets():
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
                                "target_path": "memory/agent/skills/shell.md",
                                "instruction": "新增 shell 技巧",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/skills/shell.md"]),
    ]
    signals = [TargetSignal(signal="target_skills")]
    actions = build_target_enforcement_actions(
        signals,
        turn_messages,
        current_user="yufeng",
    )
    assert len(actions) == 1
    assert actions[0].code == "persist_target_skills"
    assert actions[0].target_path_glob == "memory/agent/skills/*.md"
    assert actions[0].index_path == "memory/agent/skills/index.md"


def test_build_target_enforcement_durable_fact_requires_user_profile():
    """Writing only knowledge should not satisfy target_user_profile."""
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
                                "target_path": "memory/agent/knowledge/diet.md",
                                "instruction": "更新偏好",
                            },
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/knowledge/index.md",
                                "instruction": "更新索引",
                            },
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/knowledge/diet.md", "memory/agent/knowledge/index.md"]),
    ]
    signals = [TargetSignal(signal="target_user_profile")]
    actions = build_target_enforcement_actions(
        signals,
        turn_messages,
        current_user="yufeng",
    )
    assert len(actions) == 1
    assert actions[0].code == "persist_target_user_profile"
    assert actions[0].target_path == "memory/people/yufeng/basic-info.md"


def test_build_target_enforcement_skips_requires_persistence_false():
    turn_messages = [Message(role="assistant", content="", tool_calls=[])]
    signals = [TargetSignal(signal="target_pending_thoughts", requires_persistence=False)]
    actions = build_target_enforcement_actions(
        signals,
        turn_messages,
        current_user="yufeng",
    )
    assert actions == []


def test_detect_persistence_anomalies_missing_required_target():
    turn_messages = [Message(role="assistant", content="", tool_calls=[])]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_short_term")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_missing_required_target" for a in anomalies)


def test_detect_persistence_anomalies_missing_index_update():
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
                                "target_path": "memory/agent/skills/shell.md",
                                "instruction": "新增 shell 技巧",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/skills/shell.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_skills")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_missing_index_update" for a in anomalies)


def test_detect_persistence_anomalies_wrong_target_path():
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
                                "target_path": "memory/agent/inner-state.md",
                                "instruction": "更新情緒",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/inner-state.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_short_term")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_wrong_target_path" for a in anomalies)


def test_detect_persistence_anomalies_out_of_contract_path():
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
                                "target_path": "memory/agent/journal/2026-02-10.md",
                                "instruction": "寫入日誌",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/journal/2026-02-10.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_short_term")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_out_of_contract_path" for a in anomalies)


def test_detect_persistence_anomalies_out_of_contract_without_required_targets():
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
                                "target_path": "memory/agent/journal/2026-02-10.md",
                                "instruction": "寫入日誌",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/journal/2026-02-10.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_out_of_contract_path" for a in anomalies)


def test_detect_persistence_anomalies_brain_style_meta_text():
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
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "依照 required_actions 寫入本輪摘要",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/short-term.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_short_term")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_brain_style_meta_text" for a in anomalies)


def test_merge_anomaly_signals_deduplicates_entries():
    merged = merge_anomaly_signals(
        [AnomalySignal(signal="anomaly_missing_required_target", target_signal="target_short_term")],
        [AnomalySignal(signal="anomaly_missing_required_target", target_signal="target_short_term")],
    )
    assert len(merged) == 1


def test_format_anomaly_retry_instruction_lists_items():
    instruction = _format_anomaly_retry_instruction(
        [
            AnomalySignal(
                signal="anomaly_missing_required_target",
                target_signal="target_short_term",
                reason="missing short term",
            )
        ]
    )
    assert "anomaly_missing_required_target" in instruction
    assert "target_short_term" in instruction


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


def test_latest_intermediate_text_returns_newest():
    result = _latest_intermediate_text([
        Message(
            role="assistant",
            content="first",
            tool_calls=[ToolCall(id="t1", name="a", arguments={})],
        ),
        Message(role="tool", content="ok", tool_call_id="t1", name="a"),
        Message(
            role="assistant",
            content="second",
            tool_calls=[ToolCall(id="t2", name="b", arguments={})],
        ),
    ])
    assert result == "second"


def test_latest_intermediate_text_skips_empty():
    result = _latest_intermediate_text([
        Message(
            role="assistant",
            content="has text",
            tool_calls=[ToolCall(id="t1", name="a", arguments={})],
        ),
        Message(role="tool", content="ok", tool_call_id="t1", name="a"),
        Message(
            role="assistant",
            content="   ",
            tool_calls=[ToolCall(id="t2", name="b", arguments={})],
        ),
    ])
    assert result == "has text"


def test_latest_intermediate_text_ignores_non_tool_messages():
    result = _latest_intermediate_text([
        Message(role="assistant", content="standalone"),
    ])
    assert result == ""


def test_resolve_final_content_ignores_tool_call_draft_message():
    """Intermediate text attached to tool-call messages is NOT used as final
    content; it belongs to ProgressReviewer's domain, not PostReviewer's."""
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
    short_term = working_dir / "memory" / "agent" / "short-term.md"
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
                    "target_path": "memory/agent/short-term.md",
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

    def chat_with_tools(self, messages, tools, temperature=None):  # noqa: ANN001
        self.chat_with_tools_calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", tool_calls=[])

    def chat(self, messages, response_schema=None, temperature=None):  # noqa: ANN001
        return ""


class _CaptureConsole:
    def __init__(self):
        self.debug = False
        self.assistant_outputs: list[str] = []
        self.warnings: list[str] = []

    @contextmanager
    def spinner(self, text: str = "Thinking..."):  # noqa: ARG002
        yield

    def print_assistant(self, content: str | None):
        if content is not None:
            self.assistant_outputs.append(content)

    def print_warning(self, message: str, indent: int = 0):  # noqa: ARG002
        self.warnings.append(message)

    def print_debug(self, label: str, message: str):  # noqa: ARG002
        pass

    def print_debug_block(self, label: str, content: str):  # noqa: ARG002
        pass

    def print_tool_call(self, tool_call):  # noqa: ANN001
        pass

    def print_tool_result(self, tool_call, result):  # noqa: ANN001
        pass


class _StubProgressReviewer:
    def __init__(self, result: ProgressReviewResult | None):
        self._result = result
        self.last_raw_response: str | None = None
        self.last_error: str | None = None
        self.candidates: list[str] = []

    def review(self, messages, *, candidate_reply):  # noqa: ANN001
        self.candidates.append(candidate_reply)
        return self._result


def _ok_tool_result(tool_call_id: str, paths: list[str], turn_id: str = "turn-1") -> Message:
    """Build a successful memory_edit tool result message."""
    return Message(
        role="tool",
        name="memory_edit",
        tool_call_id=tool_call_id,
        content=json.dumps({
            "status": "ok",
            "turn_id": turn_id,
            "applied": [
                {"request_id": f"r{i+1}", "status": "applied", "path": p}
                for i, p in enumerate(paths)
            ],
            "errors": [],
        }),
    )


def _memory_edit_failed_result() -> str:
    return (
        '{"status":"failed","turn_id":"turn-x","applied":[],"errors":'
        '[{"request_id":"r1","code":"apply_failed","detail":"x"}]}'
    )


def _memory_edit_ok_result() -> str:
    return (
        '{"status":"ok","turn_id":"turn-x","applied":[{"request_id":"r1","status":"applied",'
        '"path":"memory/agent/short-term.md"}],"errors":[]}'
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
                    "target_path": "memory/agent/short-term.md",
                    "payload_text": "entry",
                }
            ],
        },
    )


def test_run_responder_skips_unregistered_tool_calls():
    """Unregistered tool calls are skipped without execution."""
    real_tc = ToolCall(id="tc1", name="memory_edit", arguments={
        "as_of": "2026-02-09T17:00:00+08:00", "turn_id": "turn-x",
        "requests": [{"request_id": "r1", "kind": "append_entry",
                       "target_path": "memory/agent/short-term.md", "payload_text": "e"}],
    })
    fake_tc = ToolCall(id="tc2", name="_post_review", arguments={})
    client = _ResponderSequenceClient([
        LLMResponse(content=None, tool_calls=[real_tc, fake_tc]),
        LLMResponse(content="done", tool_calls=[]),
    ])
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "memory_edit",
        lambda **kwargs: _memory_edit_ok_result(),  # noqa: ARG005
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
    # _post_review result should still be in conversation (as error)
    msgs = conversation.get_messages()
    post_review_results = [m for m in msgs if m.role == "tool" and m.name == "_post_review"]
    assert len(post_review_results) == 1
    assert post_review_results[0].content is not None
    assert "Unknown tool" in post_review_results[0].content


def test_run_responder_progress_review_passes_and_prints_chunk():
    tool_call = ToolCall(id="tc1", name="noop", arguments={})
    client = _ResponderSequenceClient(
        [
            LLMResponse(content="intermediate chunk", tool_calls=[tool_call]),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "noop",
        lambda **kwargs: "ok",  # noqa: ARG005
        ToolDefinition(name="noop", description="noop", parameters={}, required=[]),
    )
    console = _CaptureConsole()
    progress_reviewer = _StubProgressReviewer(
        ProgressReviewResult(
            passed=True,
            violations=[],
            block_instruction="",
        )
    )

    response = _run_responder(
        client,
        builder.build(conversation),
        registry.get_definitions(),
        conversation,
        builder,
        registry,
        console,
        progress_reviewer=progress_reviewer,
    )

    assert response.content == "done"
    tool_assistant = next(
        m for m in conversation.get_messages()
        if m.role == "assistant" and m.tool_calls
    )
    assert tool_assistant.content == "intermediate chunk"
    assert console.assistant_outputs == ["intermediate chunk"]
    assert progress_reviewer.candidates == ["intermediate chunk"]


def test_run_responder_progress_review_rejects_but_still_prints_chunk():
    tool_call = ToolCall(id="tc1", name="noop", arguments={})
    client = _ResponderSequenceClient(
        [
            LLMResponse(content="blocked chunk", tool_calls=[tool_call]),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "noop",
        lambda **kwargs: "ok",  # noqa: ARG005
        ToolDefinition(name="noop", description="noop", parameters={}, required=[]),
    )
    console = _CaptureConsole()
    progress_reviewer = _StubProgressReviewer(
        ProgressReviewResult(
            passed=False,
            violations=["simulated_user_turn"],
            block_instruction="rewrite",
        )
    )

    response = _run_responder(
        client,
        builder.build(conversation),
        registry.get_definitions(),
        conversation,
        builder,
        registry,
        console,
        progress_reviewer=progress_reviewer,
    )

    assert response.content == "done"
    tool_assistant = next(
        m for m in conversation.get_messages()
        if m.role == "assistant" and m.tool_calls
    )
    assert tool_assistant.content == "blocked chunk"
    assert console.assistant_outputs == ["blocked chunk"]
    assert "Progress-review flagged" in console.warnings[0]
    assert progress_reviewer.candidates == ["blocked chunk"]


def test_run_responder_progress_review_parse_failure_fail_open():
    tool_call = ToolCall(id="tc1", name="noop", arguments={})
    client = _ResponderSequenceClient(
        [
            LLMResponse(content="chunk with parse failure", tool_calls=[tool_call]),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "noop",
        lambda **kwargs: "ok",  # noqa: ARG005
        ToolDefinition(name="noop", description="noop", parameters={}, required=[]),
    )
    console = _CaptureConsole()
    progress_reviewer = _StubProgressReviewer(None)
    progress_reviewer.last_raw_response = "not-json"
    progress_reviewer.last_error = "parse failed"

    response = _run_responder(
        client,
        builder.build(conversation),
        registry.get_definitions(),
        conversation,
        builder,
        registry,
        console,
        progress_reviewer=progress_reviewer,
    )

    assert response.content == "done"
    tool_assistant = next(
        m for m in conversation.get_messages()
        if m.role == "assistant" and m.tool_calls
    )
    assert tool_assistant.content == "chunk with parse failure"
    assert console.assistant_outputs == ["chunk with parse failure"]
    assert console.warnings
    assert progress_reviewer.candidates == ["chunk with parse failure"]


def test_run_responder_without_progress_reviewer_prints_chunk_by_default():
    tool_call = ToolCall(id="tc1", name="noop", arguments={})
    client = _ResponderSequenceClient(
        [
            LLMResponse(content="intermediate without reviewer", tool_calls=[tool_call]),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    conversation = Conversation()
    conversation.add("user", "hi")
    builder = ContextBuilder(system_prompt="system")

    registry = ToolRegistry()
    registry.register(
        "noop",
        lambda **kwargs: "ok",  # noqa: ARG005
        ToolDefinition(name="noop", description="noop", parameters={}, required=[]),
    )
    console = _CaptureConsole()

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
    tool_assistant = next(
        m for m in conversation.get_messages()
        if m.role == "assistant" and m.tool_calls
    )
    assert tool_assistant.content == "intermediate without reviewer"
    assert console.assistant_outputs == ["intermediate without reviewer"]
    assert console.warnings == []


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


def test_run_responder_allow_failure_warns_and_continues():
    """memory_edit_allow_failure=True: warn + break instead of RuntimeError."""
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
    response = _run_responder(
        client,
        builder.build(conversation),
        registry.get_definitions(),
        conversation,
        builder,
        registry,
        console,
        memory_edit_allow_failure=True,
    )

    # Should return the last response instead of raising
    assert response is not None


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
            arguments={"path": "memory/agent/short-term.md", "content": "x"},
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
            arguments={"path": "memory/agent/short-term.md", "old_string": "old", "new_string": "new"},
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
            arguments={"command": "printf 'x' > memory/agent/short-term.md"},
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
            arguments={"command": "echo x>>memory/agent/short-term.md"},
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
            arguments={"command": "echo hi | tee memory/agent/short-term.md"},
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
            arguments={"command": "sed -i 's/a/b/' memory/agent/short-term.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_shell_rm(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=_DummyMemoryEditor(),
    )
    result = registry.execute(
        ToolCall(
            id="s4",
            name="execute_shell",
            arguments={"command": "rm memory/agent/skills/old-file.md"},
        )
    )
    assert result.startswith("Error:")
    assert "Use memory_edit" in result


def test_setup_tools_blocks_memory_shell_mv(tmp_path: Path):
    registry = setup_tools(
        ToolsConfig(),
        tmp_path,
        memory_editor=_DummyMemoryEditor(),
    )
    result = registry.execute(
        ToolCall(
            id="s5",
            name="execute_shell",
            arguments={"command": "mv memory/agent/skills/a.md memory/agent/knowledge/a.md"},
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
                        "target_path": "memory/agent/skills/demo.md",
                        "instruction": "建立 skills demo 檔案並寫入 hello",
                    }
                ],
            },
        )
    )

    assert '"status": "ok"' in result


def test_memory_edit_accepts_v2_instruction_requests(tmp_path: Path):
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
                "as_of": "2026-02-08T22:31:00+08:00",
                "turn_id": "turn-2",
                "requests": [
                    {
                        "request_id": "r1",
                        "target_path": "memory/agent/short-term.md",
                        "instruction": "追加一筆 rolling context",
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
    assert request.request_id == "r1"
    assert request.target_path == "memory/agent/short-term.md"
    assert request.instruction == "追加一筆 rolling context"


def test_memory_edit_rejects_compat_alias_fields(tmp_path: Path):
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
                "timestamp": "2026-02-08T22:32:00+08:00",
                "turn": "turn-3",
                "updates": [],
            },
        )
    )

    assert result.startswith("Error: Invalid memory_edit arguments:")
    assert "unexpected keys" in result


def test_memory_edit_rejects_legacy_kind_payload(tmp_path: Path):
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
                        "request_id": "r1",
                        "kind": "append_entry",
                        "target_path": "memory/agent/short-term.md",
                        "payload_text": "- [2026-02-09 01:08] test",
                    }
                ],
            },
        )
    )

    assert result.startswith("Error: Invalid memory_edit arguments:")
    assert "instruction" in result


def test_memory_edit_rejects_json_string_requests(tmp_path: Path):
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
                "requests": json.dumps(
                    [
                        {
                            "request_id": "r1",
                            "target_path": "memory/agent/short-term.md",
                            "instruction": "追加一筆",
                        }
                    ]
                ),
            },
        )
    )

    assert result.startswith("Error: Invalid memory_edit arguments:")


def test_build_retry_directive_without_actions_requests_final_reply():
    directive = _build_retry_directive(
        retry_instruction="回覆為空，請提供有意義的回應。",
        required_actions=[],
    )

    assert "No additional tool actions are required." in directive
    assert "Provide the final user-facing reply now." in directive
    assert "回覆為空" in directive


def test_build_missing_visible_reply_directive():
    directive = _build_missing_visible_reply_directive(
        "請提供一段最終回覆。",
        attempt=1,
        max_attempts=5,
    )

    assert "attempt: 1/5" in directive
    assert "A user-visible final reply is required for this turn." in directive
    assert "must not be empty or whitespace" in directive
    assert "請提供一段最終回覆。" in directive


def test_resolve_effective_target_signals_keeps_sticky_required_targets():
    sticky = {
        "target_thoughts": TargetSignal(
            signal="target_thoughts",
            requires_persistence=True,
            reason="carry-over unresolved target",
        )
    }
    current = [
        TargetSignal(
            signal="target_short_term",
            requires_persistence=True,
            reason="latest turn context",
        )
    ]

    effective = _resolve_effective_target_signals(current, sticky)
    names = [signal.signal for signal in effective]
    assert "target_thoughts" in names
    assert "target_short_term" in names


def test_promote_anomaly_targets_to_sticky_adds_missing_required_target():
    sticky: dict[str, TargetSignal] = {}
    anomalies = [
        AnomalySignal(
            signal="anomaly_missing_required_target",
            target_signal="target_thoughts",
            reason="missing thoughts write",
        )
    ]

    _promote_anomaly_targets_to_sticky(sticky, anomalies)

    assert "target_thoughts" in sticky
    assert sticky["target_thoughts"].requires_persistence is True


def test_detect_anomalies_wrong_target_skipped_when_all_required_satisfied():
    """When all required targets are satisfied, extra in-contract writes are OK."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-11T12:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/inner-state.md",
                                "instruction": "update inner state",
                            },
                            {
                                "request_id": "r2",
                                "target_path": "memory/people/index.md",
                                "instruction": "update people index",
                            },
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/inner-state.md", "memory/people/index.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_inner_state")],
        turn_messages,
        current_user="yufeng",
    )
    # inner-state.md satisfies the required target; people/index.md is
    # in-contract (via target_user_profile folder_prefix) so no wrong-target.
    assert not any(a.signal == "anomaly_wrong_target_path" for a in anomalies)


def test_user_profile_rule_covers_people_index():
    """memory/people/index.md should be in-contract via target_user_profile."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-11T12:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/people/index.md",
                                "instruction": "update people index",
                            },
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/people/index.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [],
        turn_messages,
        current_user="yufeng",
    )
    # people/index.md is covered by target_user_profile folder_prefix.
    assert not any(a.signal == "anomaly_out_of_contract_path" for a in anomalies)


def test_collect_memory_write_paths_partial_failure():
    """Partial success memory_edit: successful paths still collected."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T10:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "update short-term",
                            },
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/persona.md",
                                "instruction": "update persona",
                            },
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m1",
            content=json.dumps({
                "status": "failed",
                "turn_id": "turn-1",
                "applied": [
                    {"request_id": "r1", "status": "applied", "path": "memory/agent/short-term.md"},
                ],
                "errors": [
                    {"request_id": "r2", "code": "apply_failed", "detail": "parse error"},
                ],
            }),
        ),
    ]

    paths = _collect_memory_write_paths(turn_messages)
    assert "memory/agent/short-term.md" in paths
    assert "memory/agent/persona.md" not in paths


def test_collect_memory_write_paths_full_failure():
    """Fully failed memory_edit: no paths collected."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T10:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "update short-term",
                            },
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m1",
            content=json.dumps({
                "status": "failed",
                "turn_id": "turn-1",
                "applied": [],
                "errors": [
                    {"request_id": "r1", "code": "apply_failed", "detail": "x"},
                ],
            }),
        ),
    ]

    paths = _collect_memory_write_paths(turn_messages)
    assert paths == []


def test_collect_memory_write_paths_mixed_calls():
    """Mixed failed + retry success: applied paths from both results collected."""
    turn_messages = [
        # First attempt: partial failure.
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T10:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "update short-term",
                            },
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/persona.md",
                                "instruction": "update persona",
                            },
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m1",
            content=json.dumps({
                "status": "failed",
                "turn_id": "turn-1",
                "applied": [
                    {"request_id": "r1", "status": "applied", "path": "memory/agent/short-term.md"},
                ],
                "errors": [
                    {"request_id": "r2", "code": "apply_failed", "detail": "x"},
                ],
            }),
        ),
        # Retry: persona succeeds.
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m2",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T10:01:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/persona.md",
                                "instruction": "update persona",
                            },
                        ],
                    },
                )
            ],
        ),
        Message(
            role="tool",
            name="memory_edit",
            tool_call_id="m2",
            content=json.dumps({
                "status": "ok",
                "turn_id": "turn-1",
                "applied": [
                    {"request_id": "r2", "status": "applied", "path": "memory/agent/persona.md"},
                ],
                "errors": [],
            }),
        ),
    ]

    paths = _collect_memory_write_paths(turn_messages)
    assert "memory/agent/short-term.md" in paths
    assert "memory/agent/persona.md" in paths


def test_detect_persistence_anomalies_cross_attempt_satisfaction():
    """Satisfaction anomalies see writes from prior attempts (turn-scoped)."""
    # Attempt 1: wrote inner-state.
    attempt1_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T12:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/inner-state.md",
                                "instruction": "update inner state",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/inner-state.md"]),
    ]
    # Attempt 2: wrote short-term.
    attempt2_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m2",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T12:01:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "update short-term",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m2", ["memory/agent/short-term.md"]),
    ]
    turn_messages = attempt1_messages + attempt2_messages
    signals = [
        TargetSignal(signal="target_inner_state"),
        TargetSignal(signal="target_short_term"),
    ]

    anomalies = detect_persistence_anomalies(
        signals,
        turn_messages,
        current_user="yufeng",
        attempt_messages=attempt2_messages,
    )
    # Both targets satisfied across the full turn; no false positive.
    assert not any(a.signal == "anomaly_missing_required_target" for a in anomalies)


def test_detect_persistence_anomalies_behavioral_scoped_to_attempt():
    """Behavioral anomalies only see current attempt, avoiding re-triggers."""
    # Attempt 1: wrote out-of-contract path.
    attempt1_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T12:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/journal/2026-02-12.md",
                                "instruction": "write journal",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/journal/2026-02-12.md"]),
    ]
    # Attempt 2: wrote only the correct target path.
    attempt2_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m2",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T12:01:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r2",
                                "target_path": "memory/agent/short-term.md",
                                "instruction": "update short-term",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m2", ["memory/agent/short-term.md"]),
    ]
    turn_messages = attempt1_messages + attempt2_messages
    signals = [TargetSignal(signal="target_short_term")]

    anomalies = detect_persistence_anomalies(
        signals,
        turn_messages,
        current_user="yufeng",
        attempt_messages=attempt2_messages,
    )
    # The out-of-contract path from attempt 1 should NOT re-trigger in attempt 2.
    assert not any(a.signal == "anomaly_out_of_contract_path" for a in anomalies)


def test_detect_anomaly_missing_index_on_delete():
    """Deleting a folder-target file without updating index triggers anomaly."""
    turn_messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="m1",
                    name="memory_edit",
                    arguments={
                        "as_of": "2026-02-12T14:00:00+08:00",
                        "turn_id": "turn-1",
                        "requests": [
                            {
                                "request_id": "r1",
                                "target_path": "memory/agent/knowledge/old-topic.md",
                                "instruction": "delete old-topic.md",
                            }
                        ],
                    },
                )
            ],
        ),
        _ok_tool_result("m1", ["memory/agent/knowledge/old-topic.md"]),
    ]
    anomalies = detect_persistence_anomalies(
        [TargetSignal(signal="target_knowledge")],
        turn_messages,
        current_user="yufeng",
    )
    assert any(a.signal == "anomaly_missing_index_update" for a in anomalies)
