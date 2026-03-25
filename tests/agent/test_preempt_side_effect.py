"""Tests for preempting side-effect tools when fresher inbound arrives."""

from contextlib import nullcontext
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_responder
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import LLMResponse, Message, ToolCall
from chat_agent.tools.registry import ToolRegistry, ToolResult


class _Client:
    """Fake LLM client that returns queued responses."""

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
    def build(self, conversation):
        del conversation
        return [
            Message(role="system", content="sys"),
            Message(role="user", content="u"),
        ]


def _make_registry(tool_names: list[str], side_effects: set[str]) -> ToolRegistry:
    """Create a real ToolRegistry with dummy tools."""
    from chat_agent.llm.schema import ToolDefinition

    registry = ToolRegistry()
    for name in tool_names:
        defn = ToolDefinition(name=name, description=f"test {name}", parameters={})
        registry.register(name, lambda **kw: "OK", defn)
    registry.set_side_effect_tools(frozenset(side_effects))
    return registry


def _console():
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    return console


def _base_messages():
    return [
        Message(role="system", content="sys"),
        Message(role="user", content="u"),
    ]


class TestPreemptSideEffectTools:
    """Verify that side-effect tools are preempted when check_preempt returns True."""

    def test_read_only_not_preempted(self):
        """Read-only tools run even when preempt check returns True."""
        registry = _make_registry(
            ["memory_search"], side_effects=set(),
        )
        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="memory_search", arguments={}),
                ],
            ),
            # After tool loop iteration: final response
            LLMResponse(content="done", tool_calls=[]),
        ])
        preempt_calls = 0

        def _check():
            nonlocal preempt_calls
            preempt_calls += 1
            return True  # always says "new messages pending"

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=Conversation(),
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=_check,
        )
        # memory_search is NOT a side-effect tool, so preempt check
        # should never be called.
        assert preempt_calls == 0
        assert response.content == "done"

    def test_side_effect_preempted(self):
        """Side-effect tool is cancelled and turn ends immediately."""
        registry = _make_registry(
            ["memory_search", "send_message"],
            side_effects={"send_message"},
        )
        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="memory_search", arguments={}),
                    ToolCall(id="t2", name="send_message", arguments={}),
                ],
            ),
        ])

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=Conversation(),
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=lambda: True,
        )
        # Turn ends without re-querying; the new inbound will be
        # processed in the next queue cycle.
        assert client.calls == 1
        assert response.content is None
        assert response.tool_calls == []

    def test_side_effect_not_preempted_when_no_pending(self):
        """Side-effect tool runs normally when no inbound is pending."""
        registry = _make_registry(
            ["send_message"],
            side_effects={"send_message"},
        )
        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="send_message", arguments={}),
                ],
            ),
            LLMResponse(content="sent", tool_calls=[]),
        ])

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=Conversation(),
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=lambda: False,
        )
        assert response.content == "sent"

    def test_preempt_respects_max_limit(self):
        """max_preempts=0 disables preemption; side-effect tool executes."""
        registry = _make_registry(
            ["send_message"],
            side_effects={"send_message"},
        )
        preempt_calls = 0

        def _check():
            nonlocal preempt_calls
            preempt_calls += 1
            return True

        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={})],
            ),
            LLMResponse(content="finally sent", tool_calls=[]),
        ])

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=Conversation(),
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=_check,
            max_preempts=0,
        )
        assert response.content == "finally sent"
        # preempt_count (0) is never < max_preempts (0), so check is skipped
        assert preempt_calls == 0

    def test_preempt_rolls_back_conversation(self):
        """When preempted, the entire tool round is rolled back from conversation."""
        registry = _make_registry(
            ["memory_search", "send_message", "schedule_action"],
            side_effects={"send_message", "schedule_action"},
        )
        conv = Conversation()
        client = _Client([
            LLMResponse(
                content="I'll send that now",
                tool_calls=[
                    ToolCall(id="t1", name="memory_search", arguments={}),
                    ToolCall(id="t2", name="send_message", arguments={}),
                    ToolCall(id="t3", name="schedule_action", arguments={}),
                ],
            ),
        ])

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=conv,
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=lambda: True,
        )

        # Entire round (assistant draft + tool results) should be rolled back.
        assert len(conv.get_messages()) == 0
        # Response should be cleaned: no content, no tool_calls.
        assert response.content is None
        assert response.tool_calls == []

    def test_no_preempt_when_checker_is_none(self):
        """Without check_preempt, side-effect tools run normally."""
        registry = _make_registry(
            ["send_message"],
            side_effects={"send_message"},
        )
        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={})],
            ),
            LLMResponse(content="sent", tool_calls=[]),
        ])

        response = _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=Conversation(),
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=None,
        )
        assert response.content == "sent"
