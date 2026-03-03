"""Tests for send_message tool."""

from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.scope import DEFAULT_SCOPE_RESOLVER
from chat_agent.agent.shared_state import SharedStateStore
from chat_agent.agent.turn_context import TurnContext
from chat_agent.tools.builtin.send_message import (
    SEND_MESSAGE_DEFINITION,
    create_send_message,
)


def _seg(body: str, attachments: list[str] | None = None) -> dict:
    segment: dict[str, object] = {"body": body}
    if attachments is not None:
        segment["attachments"] = attachments
    return segment


def _make_tool(
    adapters=None,
    turn_context=None,
    contact_map=None,
    allowed_paths=None,
    agent_os_dir=None,
    shared_state_store=None,
    scope_resolver=None,
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
    )


class TestDefinition:
    def test_name(self):
        assert SEND_MESSAGE_DEFINITION.name == "send_message"

    def test_required_params(self):
        assert set(SEND_MESSAGE_DEFINITION.required) == {"channel", "segments"}

    def test_segments_param_exists(self):
        assert "segments" in SEND_MESSAGE_DEFINITION.parameters
        assert "body" not in SEND_MESSAGE_DEFINITION.parameters


class TestValidation:
    def test_unknown_channel(self):
        fn = _make_tool(adapters={})
        result = fn(channel="line", segments=[_seg("hi")])
        assert "Error" in result
        assert "line" in result

    def test_segments_required(self):
        fn = _make_tool(adapters={"cli": MagicMock()})
        result = fn(channel="cli", segments=[])
        assert "Error" in result
        assert "segments" in result

    def test_empty_segment_body_rejected(self):
        fn = _make_tool(adapters={"cli": MagicMock()})
        result = fn(channel="cli", segments=[_seg("   ")])
        assert "Error" in result
        assert "segments[1].body" in result

    def test_legacy_top_level_body_rejected(self):
        fn = _make_tool(adapters={"cli": MagicMock()})
        result = fn(channel="cli", segments=[_seg("hi")], body="legacy")
        assert "Error" in result
        assert "top-level 'body'" in result

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
            segments=[_seg("hi", [str(tmp_path / "missing.txt")])],
        )
        assert "Error" in result
        assert "segments[1].attachments" in result

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
        result = fn(channel="cli", segments=[_seg("hi", [str(file_path)])])
        assert "Error" in result
        assert "segments[1].attachments" in result


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

        result = fn(channel="gmail", segments=[_seg("reply content")])

        assert "OK" in result
        adapter.send.assert_called_once()
        msg = adapter.send.call_args[0][0]
        assert msg.channel == "gmail"
        assert msg.content == "reply content"
        assert msg.metadata["reply_to"] == "user@test.com"
        assert msg.metadata["thread_id"] == "t1"
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].body == "reply content"

    def test_cross_channel_gmail_requires_to(self):
        adapter = MagicMock()
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"gmail": adapter}, turn_context=ctx)

        result = fn(channel="gmail", segments=[_seg("hi")])

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
            segments=[_seg("hi there")],
        )

        assert "OK" in result
        contact_map.reverse_lookup.assert_called_once_with("gmail", "husband")
        msg = adapter.send.call_args[0][0]
        assert msg.metadata["reply_to"] == "husband@gmail.com"
        assert msg.metadata["subject"] == "Hello"


class TestSegmentDelivery:
    def test_non_gmail_multi_segments_with_route(self):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(adapters={"discord": adapter}, turn_context=ctx, contact_map=contact_map)

        result = fn(
            channel="discord",
            to="alice",
            segments=[_seg("first"), _seg("second"), _seg("third")],
        )

        assert result == "OK: sent 3 messages to discord (alice)"
        assert adapter.send.call_count == 3
        sent_bodies = [call[0][0].content for call in adapter.send.call_args_list]
        assert sent_bodies == ["first", "second", "third"]
        assert [x.body for x in ctx.pending_outbound] == ["first", "second", "third"]

    def test_segment_attachments_are_per_segment(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("a")
        f2 = tmp_path / "b.txt"
        f2.write_text("b")
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

        result = fn(
            channel="discord",
            to="alice",
            segments=[
                _seg("one", [str(f1)]),
                _seg("two", [str(f2)]),
            ],
        )

        assert result == "OK: sent 2 messages to discord (alice), 2 attachment(s)"
        msg1 = adapter.send.call_args_list[0][0][0]
        msg2 = adapter.send.call_args_list[1][0][0]
        assert msg1.attachments == [str(f1.resolve())]
        assert msg2.attachments == [str(f2.resolve())]
        assert ctx.pending_outbound[0].attachments == [str(f1.resolve())]
        assert ctx.pending_outbound[1].attachments == [str(f2.resolve())]

    def test_gmail_segments_auto_merge_single_mail(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("a")
        f2 = tmp_path / "b.txt"
        f2.write_text("b")
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "alice@gmail.com"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            allowed_paths=[str(tmp_path)],
            agent_os_dir=tmp_path,
        )

        result = fn(
            channel="gmail",
            to="alice",
            subject="S",
            segments=[
                _seg("part1", [str(f1)]),
                _seg("part2", [str(f2), str(f1)]),
            ],
        )

        assert "OK: sent to gmail (alice)" in result
        adapter.send.assert_called_once()
        msg = adapter.send.call_args[0][0]
        assert msg.content == "part1\n\npart2"
        assert msg.metadata["reply_to"] == "alice@gmail.com"
        assert msg.attachments == [str(f1.resolve()), str(f2.resolve())]
        assert len(ctx.pending_outbound) == 1
        assert ctx.pending_outbound[0].body == "part1\n\npart2"


class TestDedup:
    def test_non_gmail_dedup_by_segment(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        assert "OK" in fn(channel="discord", to="alice", segments=[_seg("hello")])
        r2 = fn(channel="discord", to="alice", segments=[_seg("hello")])
        assert "Already sent" in r2

    def test_gmail_dedup_uses_merged_payload(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "alice@gmail.com"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"gmail": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        assert "OK" in fn(
            channel="gmail",
            to="alice",
            segments=[_seg("a"), _seg("b")],
        )
        r2 = fn(
            channel="gmail",
            to="alice",
            segments=[_seg("a"), _seg("b")],
        )
        assert "Already sent" in r2

    def test_duplicate_segments_in_same_call_rejected(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": MagicMock()},
            turn_context=ctx,
            contact_map=contact_map,
        )

        r = fn(
            channel="discord",
            to="alice",
            segments=[_seg("same"), _seg("same")],
        )
        assert "Error: segments[2] duplicates segments[1]" in r

    def test_non_gmail_partial_failure_can_retry_remaining_segments(self):
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "123456"
        adapter = MagicMock()
        outcomes = [None, RuntimeError("boom"), None, None]

        def _side_effect(_msg):
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out

        adapter.send.side_effect = _side_effect
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        fn = _make_tool(
            adapters={"discord": adapter},
            turn_context=ctx,
            contact_map=contact_map,
        )

        r1 = fn(
            channel="discord",
            to="alice",
            segments=[_seg("A"), _seg("B"), _seg("C")],
        )
        assert "segments[2]" in r1
        assert "continue with remaining unsent segments" in r1

        r2 = fn(
            channel="discord",
            to="alice",
            segments=[_seg("A"), _seg("B"), _seg("C")],
        )
        assert "OK: sent 2 messages to discord (alice), skipped 1 already-sent segment(s)" == r2
        sent_bodies = [c[0][0].content for c in adapter.send.call_args_list]
        # First call: A succeeds, B fails. Retry: B/C succeed. A is not re-sent.
        assert sent_bodies == ["A", "B", "B", "C"]


class TestSharedState:
    def test_non_gmail_multi_segments_increment_shared_state_per_segment(self, tmp_path):
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

        result = fn(
            channel="discord",
            to="alice",
            segments=[_seg("one"), _seg("two")],
        )

        assert result == "OK: sent 2 messages to discord (alice)"
        assert store.get_current_rev("discord:dm:123456") == 2

    def test_gmail_merged_segments_increment_shared_state_once(self, tmp_path):
        adapter = MagicMock()
        contact_map = MagicMock(spec=ContactMap)
        contact_map.reverse_lookup.return_value = "alice@gmail.com"
        ctx = TurnContext()
        ctx.set_inbound("cli", "yufeng", {})
        store = SharedStateStore(tmp_path / "shared_state.json")
        fn = _make_tool(
            adapters={"gmail": adapter},
            turn_context=ctx,
            contact_map=contact_map,
            shared_state_store=store,
            scope_resolver=DEFAULT_SCOPE_RESOLVER,
        )

        result = fn(
            channel="gmail",
            to="alice",
            segments=[_seg("one"), _seg("two")],
        )

        assert "OK: sent to gmail (alice)" in result
        assert store.get_current_rev("gmail:sender:alice@gmail.com") == 1
