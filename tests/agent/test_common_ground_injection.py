from contextlib import nullcontext
from unittest.mock import MagicMock

from chat_agent.agent.core import _run_responder
from chat_agent.agent.responder import _make_latest_user_text_overlay
from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ContentPart, LLMResponse, Message, ToolCall
from chat_agent.tools.registry import ToolResult


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
        return ToolResult("OK")


def _conversation_cache_breakpoint(messages: list[Message]) -> Message:
    for message in messages:
        if message.role == "system" or not isinstance(message.content, list):
            continue
        first = message.content[0]
        if (
            isinstance(first, ContentPart)
            and first.cache_control == {"type": "ephemeral", "ttl": "1h"}
        ):
            return message
    raise AssertionError("conversation cache breakpoint not found")


def test_run_responder_reapplies_overlay_after_rebuild():
    client = _FakeClient()
    conversation = Conversation()
    builder = _FakeBuilder()
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    registry = _FakeRegistry()

    overlay = _make_latest_user_text_overlay(
        "[Common Ground at Message Time]\n\nscope_id: demo\nmessage_time_shared_rev: 1"
    )

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
        assert len([m for m in call_messages if m.role == "user"]) == 1
        user_msg = next(msg for msg in call_messages if msg.role == "user")
        assert "[Common Ground at Message Time]" in user_msg.content
        assert "scope_id: demo" in user_msg.content


def test_latest_user_text_overlay_appends_to_current_turn_user():
    overlay = _make_latest_user_text_overlay("[Common Ground at Message Time]\n\nscope_id: demo")
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="older"),
        Message(role="assistant", content="older reply"),
        Message(role="user", content="current"),
    ]

    result = overlay(messages)

    assert [msg.role for msg in result] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert result[3].content.startswith("current\n\n[Common Ground at Message Time]")
    assert result[3].content.endswith("scope_id: demo")


def test_run_responder_shows_thinking_block_with_char_count_for_tool_loop():
    class _ThinkingClient:
        def __init__(self):
            self._n = 0

        def chat_with_tools(self, messages, tools, temperature=None):
            del messages, tools, temperature
            self._n += 1
            if self._n == 1:
                return LLMResponse(
                    content=None,
                    reasoning_content="abc",
                    tool_calls=[ToolCall(id="t1", name="dummy", arguments={})],
                )
            return LLMResponse(content="done", tool_calls=[])

    conversation = Conversation()
    builder = _FakeBuilder()
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    registry = _FakeRegistry()

    _run_responder(
        client=_ThinkingClient(),  # type: ignore[arg-type]
        messages=[Message(role="system", content="sys"), Message(role="user", content="u")],
        tools=[],
        conversation=conversation,
        builder=builder,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=3,
        thinking_channel="discord",
        thinking_sender="alice",
    )

    console.print_inner_thoughts.assert_any_call(
        "discord",
        "alice",
        "[THINKING][chars=3]\nabc",
    )


def test_run_responder_advances_cache_breakpoint_within_same_turn():
    class _CacheClient:
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

    conversation = Conversation()
    conversation.add("user", "u1")
    conversation.add("assistant", "a1")
    conversation.add("user", "u2")

    builder = ContextBuilder(system_prompt="sys", cache_ttl="1h")
    client = _CacheClient()
    console = MagicMock()
    console.spinner.side_effect = lambda *a, **k: nullcontext()
    console.debug = False
    console.show_tool_use = False
    registry = _FakeRegistry()

    _run_responder(
        client=client,  # type: ignore[arg-type]
        messages=builder.build(conversation),
        tools=[],
        conversation=conversation,
        builder=builder,
        registry=registry,  # type: ignore[arg-type]
        console=console,  # type: ignore[arg-type]
        max_iterations=3,
    )

    assert len(client.calls) == 2

    first_breakpoint = _conversation_cache_breakpoint(client.calls[0])
    second_breakpoint = _conversation_cache_breakpoint(client.calls[1])

    assert first_breakpoint.role == "user"
    assert "u2" in first_breakpoint.content[0].text
    assert second_breakpoint.role == "user"
    assert "u2" in second_breakpoint.content[0].text
