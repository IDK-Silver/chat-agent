"""Tests for per-turn state commit tool budget."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import MagicMock

from lincy.agent.core import _run_responder
from lincy.context.conversation import Conversation
from lincy.llm.schema import LLMResponse, Message, ToolCall, ToolDefinition
from lincy.tools.registry import ToolRegistry


class _Client:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat_with_tools(self, messages, tools, temperature=None):
        del messages, tools, temperature
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no response queued")
        return self._responses.pop(0)


class _Builder:
    def build(self, conversation):
        del conversation
        return [
            Message(role="system", content="sys"),
            Message(role="user", content="u"),
        ]


def _console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _messages():
    return [
        Message(role="system", content="sys"),
        Message(role="user", content="u"),
    ]


def _register_tool(registry: ToolRegistry, name: str, func) -> None:
    registry.register(
        name,
        func,
        ToolDefinition(name=name, description=f"test {name}", parameters={}),
    )


def test_repeated_agent_note_write_stops_same_turn():
    registry = ToolRegistry()
    executed: list[str] = []

    def _agent_note(**kwargs):
        executed.append(kwargs["action"])
        return "OK: batch updated 1/1 note(s). Results: mood:changed"

    _register_tool(registry, "agent_note", _agent_note)
    conversation = Conversation()
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="agent_note",
                    arguments={
                        "action": "batch_update",
                        "updates": [{"key": "mood", "value": "專注"}],
                    },
                )
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="agent_note",
                    arguments={
                        "action": "batch_update",
                        "updates": [{"key": "location", "value": "台北"}],
                    },
                )
            ],
        ),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=conversation,
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 2
    assert executed == ["batch_update"]
    assert response.tool_calls == []
    tool_results = [
        msg for msg in conversation.get_messages()
        if msg.role == "tool" and msg.name == "agent_note"
    ]
    assert len(tool_results) == 2
    assert "SERIOUS WARNING" in (tool_results[-1].content or "")
    assert "unnecessary API cost" in (tool_results[-1].content or "")


def test_agent_note_list_does_not_consume_state_commit_budget():
    registry = ToolRegistry()
    executed: list[str] = []

    def _agent_note(**kwargs):
        executed.append(kwargs["action"])
        if kwargs["action"] == "list":
            return "No notes."
        return "OK: batch updated 1/1 note(s). Results: mood:changed"

    _register_tool(registry, "agent_note", _agent_note)
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(id="t1", name="agent_note", arguments={"action": "list"})
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="agent_note",
                    arguments={
                        "action": "batch_update",
                        "updates": [{"key": "mood", "value": "專注"}],
                    },
                )
            ],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 3
    assert executed == ["list", "batch_update"]
    assert response.content == "done"


def test_repeated_agent_note_list_stops_same_turn():
    registry = ToolRegistry()
    executed: list[str] = []

    def _agent_note(**kwargs):
        executed.append(kwargs["action"])
        return "No notes."

    _register_tool(registry, "agent_note", _agent_note)
    conversation = Conversation()
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(id="t1", name="agent_note", arguments={"action": "list"})
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(id="t2", name="agent_note", arguments={"action": "list"})
            ],
        ),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=conversation,
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 2
    assert executed == ["list"]
    assert response.tool_calls == []
    tool_results = [
        msg for msg in conversation.get_messages()
        if msg.role == "tool" and msg.name == "agent_note"
    ]
    assert len(tool_results) == 2
    assert "repeated read-only agent_note call" in (tool_results[-1].content or "")
    assert "delay the user" in (tool_results[-1].content or "")


def test_failed_memory_edit_does_not_consume_retry_budget():
    registry = ToolRegistry()
    executed = 0

    def _memory_edit(**kwargs):
        nonlocal executed
        del kwargs
        executed += 1
        status = "failed" if executed == 1 else "ok"
        return json.dumps(
            {
                "status": status,
                "turn_id": "turn-1",
                "applied": [],
                "errors": (
                    []
                    if status == "ok"
                    else [{"request_id": "r1", "code": "x", "detail": "x"}]
                ),
                "warnings": [],
            }
        )

    _register_tool(registry, "memory_edit", _memory_edit)
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="memory_edit",
                    arguments={"as_of": "now", "turn_id": "turn-1", "requests": []},
                )
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="memory_edit",
                    arguments={"as_of": "now", "turn_id": "turn-1", "requests": []},
                )
            ],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 3
    assert executed == 2
    assert response.content == "done"


def test_repeated_schedule_action_write_stops_same_turn():
    registry = ToolRegistry()
    executed: list[str] = []

    def _schedule_action(**kwargs):
        executed.append(kwargs["action"])
        return "OK: scheduled 1 action(s)\n- 2030-01-01 00:00 (1.0h from now): x"

    _register_tool(registry, "schedule_action", _schedule_action)
    conversation = Conversation()
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="schedule_action",
                    arguments={
                        "action": "batch_add",
                        "adds": [
                            {
                                "reason": "x",
                                "trigger_spec": "2030-01-01T00:00",
                            }
                        ],
                    },
                )
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="schedule_action",
                    arguments={
                        "action": "batch_add",
                        "adds": [
                            {
                                "reason": "y",
                                "trigger_spec": "2030-01-01T01:00",
                            }
                        ],
                    },
                )
            ],
        ),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=conversation,
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 2
    assert executed == ["batch_add"]
    assert response.tool_calls == []
    tool_results = [
        msg for msg in conversation.get_messages()
        if msg.role == "tool" and msg.name == "schedule_action"
    ]
    assert len(tool_results) == 2
    assert "SERIOUS WARNING" in (tool_results[-1].content or "")
    assert "batch_add" in (tool_results[-1].content or "")


def test_schedule_action_list_does_not_consume_state_commit_budget():
    registry = ToolRegistry()
    executed: list[str] = []

    def _schedule_action(**kwargs):
        executed.append(kwargs["action"])
        if kwargs["action"] == "list":
            return "No pending scheduled actions."
        return "OK: scheduled 1 action(s)\n- 2030-01-01 00:00 (1.0h from now): x"

    _register_tool(registry, "schedule_action", _schedule_action)
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(id="t1", name="schedule_action", arguments={"action": "list"})
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="schedule_action",
                    arguments={
                        "action": "batch_add",
                        "adds": [
                            {
                                "reason": "x",
                                "trigger_spec": "2030-01-01T00:00",
                            }
                        ],
                    },
                )
            ],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 3
    assert executed == ["list", "batch_add"]
    assert response.content == "done"


def test_repeated_schedule_action_list_stops_same_turn():
    registry = ToolRegistry()
    executed: list[str] = []

    def _schedule_action(**kwargs):
        executed.append(kwargs["action"])
        return "No pending scheduled actions."

    _register_tool(registry, "schedule_action", _schedule_action)
    conversation = Conversation()
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(id="t1", name="schedule_action", arguments={"action": "list"})
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(id="t2", name="schedule_action", arguments={"action": "list"})
            ],
        ),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=conversation,
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 2
    assert executed == ["list"]
    assert response.tool_calls == []
    tool_results = [
        msg for msg in conversation.get_messages()
        if msg.role == "tool" and msg.name == "schedule_action"
    ]
    assert len(tool_results) == 2
    assert "repeated read-only schedule_action call" in (
        tool_results[-1].content or ""
    )


def test_failed_schedule_action_does_not_consume_retry_budget():
    registry = ToolRegistry()
    executed = 0

    def _schedule_action(**kwargs):
        nonlocal executed
        del kwargs
        executed += 1
        if executed == 1:
            return "Error: invalid datetime format: 'bad'"
        return "OK: scheduled 1 action(s)\n- 2030-01-01 00:00 (1.0h from now): x"

    _register_tool(registry, "schedule_action", _schedule_action)
    client = _Client([
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="schedule_action",
                    arguments={
                        "action": "batch_add",
                        "adds": [{"reason": "x", "trigger_spec": "bad"}],
                    },
                )
            ],
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="t2",
                    name="schedule_action",
                    arguments={
                        "action": "batch_add",
                        "adds": [
                            {
                                "reason": "x",
                                "trigger_spec": "2030-01-01T00:00",
                            }
                        ],
                    },
                )
            ],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])

    response = _run_responder(
        client=client,
        messages=_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),
        registry=registry,
        console=_console(),
        max_iterations=5,
    )

    assert client.calls == 3
    assert executed == 2
    assert response.content == "done"
