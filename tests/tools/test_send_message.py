"""Tests for send_message tool."""

from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.scope import DEFAULT_SCOPE_RESOLVER
from chat_agent.agent.shared_state import SharedStateStore
from chat_agent.agent.turn_context import TurnContext
from chat_agent.tools.builtin.send_message import (
    SEND_MESSAGE_DEFINITION,
    build_send_message_definition,
    create_send_message,
)


def _make_tool(
    adapters=None,
    turn_context=None,
    contact_map=None,
    allowed_paths=None,
    agent_os_dir=None,
    shared_state_store=None,
    scope_resolver=None,
    pending_scope_check=None,
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
        shared_state_store=shared_state_store,
        scope_resolver=scope_resolver,
        pending_scope_check=pending_scope_check,
    )


class TestDefinition:
    def test_name(self):
        assert SEND_MESSAGE_DEFINITION.name == "send_message"

    def test_required_params(self):
        assert set(SEND_MESSAGE_DEFINITION.required) == {"channel", "body"}

    def test_body_param_exists(self):
        assert "body" in SEND_MESSAGE_DEFINITION.parameters
        assert "segments" not in SEND_MESSAGE_DEFINITION.parameters

    def test_batch_guidance_can_be_disabled(self):
        definition = build_send_message_definition(batch_guidance_enabled=False)
        assert "splitting across rounds" not in definition.description


class TestValidation:
    def test_unknown_channel(self):
        fn = _make_tool(adapters={})
        result = fn(channel="line", body="hi")
        assert "Error" in result
        assert "line" in result

    def test_empty_body_rejected(self):
        fn = _make_tool(adapters={"cli": MagicMock()})
        result = fn(channel="cli", body="   ")
        assert "Error" in result
        assert "body" in result

    def test_attachment_not_found(self, tmp_path):
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            agent_os_dir=tmp_path,
        )
        result = fn(
            channel="cli",
            body="hi",
            attachments=[str(tmp_path / "missing.txt")],
        )
        assert "Error" in result
        assert "not found" in result

    def test_attachment_path_not_allowed(self, tmp_path):
        file_path = tmp_path / "secret.txt"
        file_path.write_text("secret")
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()

        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"cli": MagicMock()},
            turn_context=ctx,
            allowed_paths=[str(allowed_dir)],
            agent_os_dir=allowed_dir,
        )
        result = fn(channel="cli", body="hi", attachments=[str(file_path)])
        assert "Error" in result
        assert "not allowed" in result


class TestRouting:
    def test_reply_same_channel(self):
        adapter = MagicMock()
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
        # Gmail replies must preserve message_id for In-Reply-To header;
        # without it the recipient sees a new thread instead of a reply.
        assert msg.metadata["message_id"] == "m1"
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].body == "reply content"

    def test_discord_reply_does_not_inherit_message_id(self):
        """Discord should NOT inherit inbound message_id to avoid unwanted reply refs."""
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("discord", "friend", {
            "reply_to": "123",
            "channel_id": "ch1",
            "message_id": "discord_msg_1",
        })
        fn = _make_tool(adapters={"discord": adapter}, turn_context=ctx)

        result = fn(channel="discord", body="yo")

        assert "OK" in result
        msg = adapter.send.call_args[0][0]
        assert "message_id" not in msg.metadata

    def test_cross_channel_gmail_requires_to(self):
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"gmail": adapter}, turn_context=ctx)

        result = fn(channel="gmail", body="hi")

        assert "Error" in result
        assert "'to' is required" in result
        adapter.send.assert_not_called()

    def test_send_to_named_recipient(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "husband@gmail.com"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            contact_map=contact_map,
        )

        result = fn(
            channel="gmail",
            to="husband",
            subject="Hello",
            body="hi there",
        )

        assert "OK" in result
        contact_map.reverse_lookup.assert_called_once_with("gmail", "husband")
        msg = adapter.send.call_args[0][0]
        assert msg.metadata["reply_to"] == "husband@gmail.com"
        assert msg.metadata["subject"] == "Hello"


class TestDelivery:
    def test_single_message_delivery(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"discord": adapter}, turn_context=ctx, contact_map=contact_map)

        result = fn(channel="discord", to="alice", body="hello")

        assert result == "OK: sent to discord (alice)"
        adapter.send.assert_called_once()
        assert adapter.send.call_args[0][0].content == "hello"
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].body == "hello"

    def test_attachment_delivery(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("a")
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            allowed_paths=[str(tmp_path)],
            agent_os_dir=tmp_path,
        )

        result = fn(channel="discord", to="alice", body="file", attachments=[str(f1)])

        assert "OK: sent to discord (alice), 1 attachment(s)" == result
        msg = adapter.send.call_args[0][0]
        assert msg.attachments == [str(f1.resolve())]
        assert ctx.pending_outbound[0].attachments == [str(f1.resolve())]

    def test_cli_channel_does_not_call_adapter_send(self):
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"cli": adapter}, turn_context=ctx)

        result = fn(channel="cli", body="report")

        assert "OK" in result
        adapter.send.assert_not_called()
        assert len(ctx.pending_outbound) == 1

    def test_adapter_failure_returns_error(self):
        adapter = MagicMock()
        adapter.send.side_effect = RuntimeError("boom")
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"discord": adapter}, turn_context=ctx, contact_map=contact_map)

        result = fn(channel="discord", to="alice", body="fail")

        assert "Error" in result
        assert "failed to deliver" in result

    def test_system_turn_yields_to_newer_pending_inbound(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("system", "system", {"scheduled_reason": "meds"})
        fn = _make_tool(
            adapters={"discord": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            scope_resolver=DEFAULT_SCOPE_RESOLVER,
            pending_scope_check=lambda scope_id: scope_id == "discord:dm:123456",
        )

        result = fn(channel="discord", to="alice", body="hello")

        assert "yielded proactive send" in result
        adapter.send.assert_not_called()
        assert ctx.proactive_yield is not None
        assert ctx.proactive_yield.scope_id == "discord:dm:123456"
        assert ctx.pending_outbound == []
        assert ctx.sent_hashes == set()

    def test_non_system_turn_does_not_yield(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("discord", "friend", {"author_id": "friend"})
        fn = _make_tool(
            adapters={"discord": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            scope_resolver=DEFAULT_SCOPE_RESOLVER,
            pending_scope_check=lambda scope_id: scope_id == "discord:dm:123456",
        )

        result = fn(channel="discord", to="alice", body="hello")

        assert result == "OK: sent to discord (alice)"
        adapter.send.assert_called_once()
        assert ctx.proactive_yield is None


class TestDedup:
    def test_dedup_same_body_within_turn(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        assert "OK" in fn(channel="discord", to="alice", body="hello")
        r2 = fn(channel="discord", to="alice", body="hello")
        assert "Already sent" in r2

    def test_different_body_not_deduped(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        assert "OK" in fn(channel="discord", to="alice", body="a")
        assert "OK" in fn(channel="discord", to="alice", body="b")


class TestSharedState:
    def test_shared_state_recorded_on_send(self, tmp_path):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("discord", "friend", {"is_dm": True, "author_id": "123", "channel_id": "dm1"})
        store = SharedStateStore(tmp_path / "shared_state.json")
        fn = _make_tool(
            adapters={"discord": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            shared_state_store=store,
            scope_resolver=DEFAULT_SCOPE_RESOLVER,
        )

        result = fn(channel="discord", to="alice", body="hello")

        assert result == "OK: sent to discord (alice)"
        assert store.get_current_rev("discord:dm:123456") == 1
