"""Tests for send_message tool."""

from unittest.mock import MagicMock

from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.turn_context import TurnContext
from chat_agent.tools.builtin.send_message import (
    SEND_MESSAGE_DEFINITION,
    create_send_message,
)


def _make_tool(adapters=None, turn_context=None, contact_map=None, console=None):
    if adapters is None:
        adapters = {}
    if turn_context is None:
        turn_context = TurnContext()
    if contact_map is None:
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = None
    if console is None:
        console = MagicMock()
    return create_send_message(adapters, turn_context, contact_map, console)


class TestDefinition:
    def test_name(self):
        assert SEND_MESSAGE_DEFINITION.name == "send_message"

    def test_required_params(self):
        assert set(SEND_MESSAGE_DEFINITION.required) == {"channel", "body"}


class TestReplyMode:
    def test_reply_same_channel(self):
        adapter = MagicMock()
        adapter.channel_name = "gmail"
        ctx = TurnContext()
        ctx.set_inbound("gmail", "user@test.com", {
            "reply_to": "user@test.com",
            "subject": "Re: Hello",
            "thread_id": "t1",
            "message_id": "m1",
        })
        console = MagicMock()
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            console=console,
        )

        result = fn(channel="gmail", body="reply content")

        assert "OK" in result
        adapter.send.assert_called_once()
        msg = adapter.send.call_args[0][0]
        assert msg.channel == "gmail"
        assert msg.content == "reply content"
        assert msg.metadata["reply_to"] == "user@test.com"
        assert msg.metadata["thread_id"] == "t1"
        console.print_outbound.assert_called_once()

    def test_reply_subject_override(self):
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("gmail", "u@t.com", {
            "reply_to": "u@t.com",
            "subject": "Old Subject",
        })
        fn = _make_tool(adapters={"gmail": adapter}, turn_context=ctx)

        fn(channel="gmail", body="hi", subject="New Subject")

        msg = adapter.send.call_args[0][0]
        assert msg.metadata["subject"] == "New Subject"


class TestCrossChannel:
    def test_send_to_cli(self):
        cli_adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("gmail", "stranger@test.com", {})
        console = MagicMock()
        fn = _make_tool(
            adapters={"cli": cli_adapter, "gmail": MagicMock()},
            turn_context=ctx,
            console=console,
        )

        result = fn(channel="cli", body="report to operator")

        assert "OK" in result
        assert "cli" in result
        # CLI adapter.send is not called (display via console)
        cli_adapter.send.assert_not_called()
        console.print_outbound.assert_called_once_with("cli", None, "report to operator")


class TestProactiveMessage:
    def test_send_to_named_recipient(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "husband@gmail.com"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})  # inbound is CLI
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            contact_map=contact_map,
        )

        result = fn(channel="gmail", to="husband", body="hi there", subject="Hello")

        assert "OK" in result
        contact_map.reverse_lookup.assert_called_once_with("gmail", "husband")
        msg = adapter.send.call_args[0][0]
        assert msg.metadata["reply_to"] == "husband@gmail.com"
        assert msg.metadata["subject"] == "Hello"

    def test_reverse_lookup_miss(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = None
        fn = _make_tool(adapters={"gmail": adapter}, contact_map=contact_map)

        result = fn(channel="gmail", to="unknown", body="hi")

        assert "Error" in result
        adapter.send.assert_not_called()


class TestErrors:
    def test_unknown_channel(self):
        fn = _make_tool(adapters={})
        result = fn(channel="line", body="hi")
        assert "Error" in result
        assert "line" in result

    def test_empty_body(self):
        fn = _make_tool(adapters={"cli": MagicMock()})
        result = fn(channel="cli", body="   ")
        assert "Error" in result
        assert "empty" in result

    def test_gmail_cross_channel_requires_to(self):
        """Gmail messages outside reply mode require explicit 'to'."""
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})  # inbound is CLI, not gmail
        fn = _make_tool(adapters={"gmail": adapter}, turn_context=ctx)

        result = fn(channel="gmail", body="hi")

        assert "Error" in result
        adapter.send.assert_not_called()
