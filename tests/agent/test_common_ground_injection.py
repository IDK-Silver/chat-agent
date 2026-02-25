from contextlib import nullcontext
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_responder
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import LLMResponse, Message, ToolCall


class _FakeClient:
    def __init__(self):
        self.calls: list[list[Message]] = []
        self._n = 0

    def chat_with_tools(self, messages, tools, temperature=None):
        del tools, temperature
        self.calls.append(list(messages))
        self._n += 1
        if self._n == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="dummy", arguments={})],
            )
        return LLMResponse(content="done", tool_calls=[])


class _FakeBuilder:
    def __init__(self):
        self.calls = 0

    def build(self, conversation):
        del conversation
        self.calls += 1
        return [Message(role="system", content="sys"), Message(role="user", content="u")]


class _FakeRegistry:
    def has_tool(self, name):
        return name == "dummy"

    def execute(self, tool_call):
        del tool_call
        return "OK"


def test_run_responder_reapplies_overlay_after_rebuild():
    client = _FakeClient()
    conversation = Conversation()
    builder = _FakeBuilder()
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    registry = _FakeRegistry()

    extra = [
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="cg_anchor_0", name="_load_common_ground_at_message_time", arguments={})],
        ),
        Message(
            role="tool",
            content="[Common Ground at Message Time]",
            tool_call_id="cg_anchor_0",
            name="_load_common_ground_at_message_time",
        ),
    ]

    def overlay(messages):
        return [*messages, *extra]

    _run_responder(
        client=client,
        messages=[Message(role="system", content="sys"), Message(role="user", content="u")],
        tools=[],
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=3,
        message_overlay=overlay,
    )

    assert len(client.calls) == 2
    for call_messages in client.calls:
        assert any(m.role == "tool" and m.name == "_load_common_ground_at_message_time" for m in call_messages)

