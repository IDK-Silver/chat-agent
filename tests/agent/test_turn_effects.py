"""Unit tests for scheduled-turn effect analysis."""

import json

from chat_agent.agent.turn_effects import analyze_turn_effects
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ToolCall


def _build_turn(*, tool_calls: list[ToolCall], results: dict[str, str]) -> list:
    conv = Conversation()
    conv.add("user", "[SCHEDULED]", channel="system", sender="system")
    conv.add_assistant_with_tools(None, tool_calls)
    for tc in tool_calls:
        conv.add_tool_result(tc.id, tc.name, results[tc.id])
    return conv.get_messages()


class TestAnalyzeTurnEffects:
    def test_schedule_list_success_not_mutation(self):
        msgs = _build_turn(
            tool_calls=[ToolCall(id="c1", name="schedule_action", arguments={"action": "list"})],
            results={"c1": "No pending scheduled actions."},
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_schedule_mutation is False
        assert effects.is_scheduled_noop is True

    def test_schedule_add_success_is_mutation(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="schedule_action",
                    arguments={"action": "add", "reason": "x", "trigger_spec": "2026-02-23T22:00"},
                )
            ],
            results={"c1": "OK: scheduled at 2026-02-23 22:00 (2.0h from now)"},
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_schedule_mutation is True
        assert effects.is_scheduled_noop is False

    def test_schedule_remove_failure_not_mutation(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="schedule_action",
                    arguments={"action": "remove", "pending_id": "x.json"},
                )
            ],
            results={"c1": "Error: pending message not found: x.json"},
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_schedule_mutation is False
        assert effects.is_scheduled_noop is True

    def test_memory_edit_applied_counts_as_side_effect(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="memory_edit",
                    arguments={"as_of": "t", "turn_id": "t1", "requests": []},
                )
            ],
            results={
                "c1": json.dumps(
                    {
                        "status": "ok",
                        "turn_id": "t1",
                        "applied": [
                            {
                                "request_id": "r1",
                                "status": "applied",
                                "path": "memory/agent/recent.md",
                            }
                        ],
                        "errors": [],
                        "warnings": [],
                    }
                )
            },
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_memory_edit_applied is True
        assert effects.is_scheduled_noop is False

    def test_memory_edit_noop_not_side_effect(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="memory_edit",
                    arguments={"as_of": "t", "turn_id": "t1", "requests": []},
                )
            ],
            results={
                "c1": json.dumps(
                    {
                        "status": "ok",
                        "turn_id": "t1",
                        "applied": [
                            {
                                "request_id": "r1",
                                "status": "noop",
                                "path": "memory/agent/recent.md",
                            }
                        ],
                        "errors": [],
                        "warnings": [],
                    }
                )
            },
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_memory_edit_applied is False
        assert effects.is_scheduled_noop is True

    def test_memory_edit_partial_failure_with_applied_counts(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="memory_edit",
                    arguments={"as_of": "t", "turn_id": "t1", "requests": []},
                )
            ],
            results={
                "c1": json.dumps(
                    {
                        "status": "failed",
                        "turn_id": "t1",
                        "applied": [
                            {
                                "request_id": "r1",
                                "status": "applied",
                                "path": "memory/agent/recent.md",
                            }
                        ],
                        "errors": [
                            {"request_id": "r2", "code": "x", "detail": "failed"}
                        ],
                        "warnings": [],
                    }
                )
            },
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_memory_edit_applied is True
        assert effects.is_scheduled_noop is False

    def test_send_message_flag_short_circuits_noop(self):
        msgs = _build_turn(
            tool_calls=[ToolCall(id="c1", name="schedule_action", arguments={"action": "list"})],
            results={"c1": "No pending scheduled actions."},
        )
        effects = analyze_turn_effects(msgs, had_send_message=True)
        assert effects.had_send_message is True
        assert effects.is_scheduled_noop is False

    def test_agent_task_create_counts_as_mutation(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="agent_task",
                    arguments={"action": "create", "title": "準備會議"},
                )
            ],
            results={"c1": "OK: created [t_0001] 準備會議"},
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_task_mutation is True
        assert effects.is_scheduled_noop is False

    def test_agent_note_update_counts_as_mutation(self):
        msgs = _build_turn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="agent_note",
                    arguments={"action": "update", "key": "meeting_context"},
                )
            ],
            results={"c1": "OK: updated note 'meeting_context'"},
        )
        effects = analyze_turn_effects(msgs, had_send_message=False)
        assert effects.had_note_mutation is True
        assert effects.is_scheduled_noop is False
