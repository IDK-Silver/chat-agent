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
        """Side-effect tool is cancelled when fresher inbound exists."""
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
            # After preempt: LLM reconsiders with new inbound
            LLMResponse(content="reconsidered", tool_calls=[]),
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
        assert response.content == "reconsidered"
        assert client.calls == 2

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
        """After max_preempts, side-effect tools execute without checking."""
        registry = _make_registry(
            ["send_message"],
            side_effects={"send_message"},
        )
        preempt_calls = 0

        def _check():
            nonlocal preempt_calls
            preempt_calls += 1
            return True

        # 3 rounds: preempted twice, then executes on third
        client = _Client([
            # Round 1: preempted
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t1", name="send_message", arguments={})],
            ),
            # Round 2: preempted again
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t2", name="send_message", arguments={})],
            ),
            # Round 3: max_preempts=2 reached, executes normally
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="t3", name="send_message", arguments={})],
            ),
            # Final response after successful execution
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
            max_preempts=2,
        )
        assert response.content == "finally sent"
        assert preempt_calls == 2  # checked twice, hit limit

    def test_cancelled_tools_get_error_results(self):
        """When preempted, all remaining tool calls get cancelled results in conversation."""
        registry = _make_registry(
            ["memory_search", "send_message", "schedule_action"],
            side_effects={"send_message", "schedule_action"},
        )
        conv = Conversation()
        client = _Client([
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="t1", name="memory_search", arguments={}),
                    ToolCall(id="t2", name="send_message", arguments={}),
                    ToolCall(id="t3", name="schedule_action", arguments={}),
                ],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ])

        _run_responder(
            client=client,
            messages=_base_messages(),
            tools=[],
            conversation=conv,
            builder=_Builder(),
            registry=registry,
            console=_console(),
            check_preempt=lambda: True,
        )

        # Inspect conversation: t1 should have OK result,
        # t2 and t3 should have preempted error results.
        messages = conv.get_messages()
        tool_results = [m for m in messages if m.role == "tool"]
        assert len(tool_results) == 3

        # t1: memory_search executed normally
        assert tool_results[0].name == "memory_search"
        assert "preempted" not in (tool_results[0].content or "")

        # t2: send_message cancelled
        assert tool_results[1].name == "send_message"
        assert "preempted" in (tool_results[1].content or "")

        # t3: schedule_action cancelled
        assert tool_results[2].name == "schedule_action"
        assert "preempted" in (tool_results[2].content or "")

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
