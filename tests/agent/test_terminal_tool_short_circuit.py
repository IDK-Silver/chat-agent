from contextlib import nullcontext
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_responder
from chat_agent.context.conversation import Conversation
from chat_agent.core.schema import ToolsConfig
from chat_agent.llm.schema import LLMResponse, Message, ToolCall
from chat_agent.tools.registry import ToolResult


class _Client:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls = 0

    def chat_with_tools(self, messages, tools, temperature=None):
        del messages, tools, temperature
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no response queued")
        return self._responses.pop(0)


class _Builder:
    def __init__(self):
        self.calls = 0

    def build(self, conversation):
        del conversation
        self.calls += 1
        return [Message(role="system", content="sys"), Message(role="user", content="u")]


class _Registry:
    def __init__(self, results: dict[str, str]):
        self._results = dict(results)

    def has_tool(self, name):
        return name in self._results

    def execute(self, tool_call):
        content = self._results[tool_call.name]
        is_error = isinstance(content, str) and content.startswith("Error")
        return ToolResult(content, is_error=is_error)


def _console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _base_messages():
    return [Message(role="system", content="sys"), Message(role="user", content="u")]


def test_short_circuit_send_message_success():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"})],
            ),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"send_message": "OK: sent to cli"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 1
    assert response.finish_reason == "terminal_tool_short_circuit"
    assert response.tool_calls == []


def test_short_circuit_skips_on_tool_error():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"send_message": "Error: failed"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 2
    assert response.content == "done"


def test_short_circuit_skips_schedule_action_list():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="schedule_action", arguments={"action": "list"})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"schedule_action": "No pending scheduled actions."}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 2
    assert response.content == "done"


def test_short_circuit_allows_schedule_action_add():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="schedule_action", arguments={"action": "add"})],
            ),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"schedule_action": "OK: scheduled at 2026-03-03 12:00 (1.0h from now)"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 1
    assert response.finish_reason == "terminal_tool_short_circuit"


def test_short_circuit_allows_schedule_action_remove():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="schedule_action", arguments={"action": "remove"})],
            ),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"schedule_action": "OK: removed abc.json"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 1
    assert response.finish_reason == "terminal_tool_short_circuit"


def test_short_circuit_allows_multiple_terminal_tools_in_one_round():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"}),
                    ToolCall(id="t2", name="schedule_action", arguments={"action": "add"}),
                ],
            ),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({
            "send_message": "OK: sent to cli",
            "schedule_action": "OK: scheduled at 2026-03-03 12:00 (1.0h from now)",
        }),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 1
    assert response.finish_reason == "terminal_tool_short_circuit"


def test_short_circuit_disabled_uses_normal_loop():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"send_message": "OK: sent to cli"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig.model_validate(
            {"terminal_tool_short_circuit": {"enabled": False}}
        ),
    )
    assert client.calls == 2
    assert response.content == "done"


def test_short_circuit_skips_when_tool_not_allowed():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="memory_edit", arguments={})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"memory_edit": "OK"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 2
    assert response.content == "done"


def test_short_circuit_skips_when_round_has_mixed_terminal_and_non_terminal_tools():
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"}),
                    ToolCall(id="t2", name="memory_edit", arguments={}),
                ],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({
            "send_message": "OK: sent to cli",
            "memory_edit": "OK",
        }),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 2
    assert response.content == "done"


def test_short_circuit_skips_on_registry_exception_error():
    """Regression: 'Error executing ...' from registry exceptions must block short-circuit."""
    client = _Client(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={"channel": "cli", "body": "hi"})],
            ),
            LLMResponse(content="retried", tool_calls=[]),
        ]
    )
    response = _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=_base_messages(),
        tools=[],
        conversation=Conversation(),
        builder=_Builder(),  # type: ignore[arg-type]
        registry=_Registry({"send_message": "Error executing send_message: missing arg"}),  # type: ignore[arg-type]
        console=_console(),  # type: ignore[arg-type]
        tools_config=ToolsConfig(),
    )
    assert client.calls == 2
    assert response.content == "retried"
