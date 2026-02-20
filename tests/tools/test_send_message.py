"""Tests for send_message tool."""

from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.turn_context import TurnContext
from chat_agent.tools.builtin.send_message import (
    SEND_MESSAGE_DEFINITION,
    create_send_message,
)


def _make_tool(
    adapters=None,
    turn_context=None,
    contact_map=None,
    allowed_paths=None,
    agent_os_dir=None,
):
    if adapters is None:
        adapters = {}
    if turn_context is None:
        turn_context = TurnContext()
    if contact_map is None:
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = None
    return create_send_message(
        adapters,
        turn_context,
        contact_map,
        allowed_paths=allowed_paths,
        agent_os_dir=agent_os_dir,
    )


class TestDefinition:
    def test_name(self):
        assert SEND_MESSAGE_DEFINITION.name == "send_message"

    def test_required_params(self):
        assert set(SEND_MESSAGE_DEFINITION.required) == {"channel", "body"}

    def test_attachments_param_exists(self):
        assert "attachments" in SEND_MESSAGE_DEFINITION.parameters


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
        fn = _make_tool(adapters={"gmail": adapter}, turn_context=ctx)

        result = fn(channel="gmail", body="reply content")

        assert "OK" in result
        adapter.send.assert_called_once()
        msg = adapter.send.call_args[0][0]
        assert msg.channel == "gmail"
        assert msg.content == "reply content"
        assert msg.metadata["reply_to"] == "user@test.com"
        assert msg.metadata["thread_id"] == "t1"
        # Outbound buffered in turn_context (not displayed immediately)
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].body == "reply content"

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
        fn = _make_tool(
            adapters={"cli": cli_adapter, "gmail": MagicMock()},
            turn_context=ctx,
        )

        result = fn(channel="cli", body="report to operator")

        assert "OK" in result
        assert "cli" in result
        # CLI adapter.send is not called (display via console flush)
        cli_adapter.send.assert_not_called()
        # Buffered for deferred display
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].channel == "cli"
        assert ctx.pending_outbound[0].body == "report to operator"


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


class TestDedup:
    def test_duplicate_send_blocked(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        result1 = fn(channel="cli", body="hello")
        assert "OK" in result1
        assert len(ctx.pending_outbound) == 1

        result2 = fn(channel="cli", body="hello")
        assert "Already sent" in result2
        assert len(ctx.pending_outbound) == 1  # no second buffer

    def test_different_body_not_blocked(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        assert "OK" in fn(channel="cli", body="hello")
        assert "OK" in fn(channel="cli", body="goodbye")
        assert len(ctx.pending_outbound) == 2

    def test_different_channel_not_blocked(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "u@t.com"
        fn = _make_tool(
            adapters={"cli": MagicMock(), "gmail": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        assert "OK" in fn(channel="cli", body="hello")
        assert "OK" in fn(channel="gmail", body="hello", to="husband")

    def test_dedup_resets_on_new_turn(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        assert "OK" in fn(channel="cli", body="hello")
        assert "Already sent" in fn(channel="cli", body="hello")

        # New turn resets dedup
        ctx.set_inbound("cli", "yufeng", {})
        assert "OK" in fn(channel="cli", body="hello")


class TestBuffering:
    def test_outbound_buffered_not_immediate(self):
        """send_message buffers in turn_context, not console."""
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        fn(channel="cli", body="hi")

        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].channel == "cli"
        assert ctx.pending_outbound[0].recipient == "yufeng"
        assert ctx.pending_outbound[0].body == "hi"

    def test_multiple_messages_buffered_in_order(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "a@b.com"
        fn = _make_tool(
            adapters={"cli": MagicMock(), "gmail": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        fn(channel="cli", body="first")
        fn(channel="gmail", body="second", to="friend")

        assert len(ctx.pending_outbound) == 2
        assert ctx.pending_outbound[0].body == "first"
        assert ctx.pending_outbound[1].body == "second"
        assert ctx.pending_outbound[1].channel == "gmail"

    def test_buffer_cleared_on_set_inbound(self):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        fn(channel="cli", body="hi")
        assert len(ctx.pending_outbound) == 1

        ctx.set_inbound("cli", "yufeng", {})
        assert len(ctx.pending_outbound) == 0


class TestAttachments:
    def test_send_with_attachments(self, tmp_path):
        """Valid attachments appear in OutboundMessage and PendingOutbound."""
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-test")
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("gmail", "u@t.com", {
            "reply_to": "u@t.com",
            "subject": "Re: Files",
        })
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            allowed_paths=[str(tmp_path)],
            agent_os_dir=tmp_path,
        )

        result = fn(channel="gmail", body="see attached", attachments=[str(f)])

        assert "OK" in result
        assert "1 attachment" in result
        # OutboundMessage carries resolved path
        msg = adapter.send.call_args[0][0]
        assert len(msg.attachments) == 1
        assert msg.attachments[0] == str(f.resolve())
        # PendingOutbound also carries attachments
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].attachments == [str(f.resolve())]

    def test_attachment_not_found(self, tmp_path):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            agent_os_dir=tmp_path,
        )

        result = fn(
            channel="cli", body="hi",
            attachments=[str(tmp_path / "nonexistent.txt")],
        )

        assert "Error" in result
        assert "not found" in result

    def test_attachment_path_not_allowed(self, tmp_path):
        # Create file outside allowed paths
        f = tmp_path / "secret.txt"
        f.write_text("secret")
        other_dir = tmp_path / "allowed"
        other_dir.mkdir()
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            allowed_paths=[str(other_dir)],
            agent_os_dir=other_dir,
        )

        result = fn(channel="cli", body="hi", attachments=[str(f)])

        assert "Error" in result
        assert "not allowed" in result

    def test_no_attachments_backward_compat(self):
        """No attachments = empty list, existing behavior unchanged."""
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": MagicMock()}, turn_context=ctx)

        result = fn(channel="cli", body="hello")

        assert "OK" in result
        assert "attachment" not in result
        assert ctx.pending_outbound[0].attachments == []

    def test_dedup_includes_attachments(self, tmp_path):
        """Same body but different attachments should not be deduped."""
        f1 = tmp_path / "a.txt"
        f1.write_text("a")
        f2 = tmp_path / "b.txt"
        f2.write_text("b")
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            allowed_paths=[str(tmp_path)],
            agent_os_dir=tmp_path,
        )

        r1 = fn(channel="cli", body="hello", attachments=[str(f1)])
        assert "OK" in r1

        r2 = fn(channel="cli", body="hello", attachments=[str(f2)])
        assert "OK" in r2

        assert len(ctx.pending_outbound) == 2

    def test_dedup_same_attachments_blocked(self, tmp_path):
        """Same body + same attachments should be deduped."""
        f = tmp_path / "a.txt"
        f.write_text("a")
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            allowed_paths=[str(tmp_path)],
            agent_os_dir=tmp_path,
        )

        assert "OK" in fn(channel="cli", body="hello", attachments=[str(f)])
        assert "Already sent" in fn(channel="cli", body="hello", attachments=[str(f)])
        assert len(ctx.pending_outbound) == 1
