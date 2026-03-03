"""Tests for TurnContext."""

from chat_agent.agent.turn_context import PendingOutbound, TurnContext


class TestTurnContext:
    def test_defaults(self):
        ctx = TurnContext()
        assert ctx.channel == "cli"
        assert ctx.sender is None
        assert ctx.metadata == {}

    def test_set_inbound(self):
        ctx = TurnContext()
        meta = {"reply_to": "a@b.com", "thread_id": "t1"}
        ctx.set_inbound("gmail", "a@b.com", meta)
        assert ctx.channel == "gmail"
        assert ctx.sender == "a@b.com"
        assert ctx.metadata == meta
        assert ctx.sent_hashes == set()
        assert ctx.pending_outbound == []

    def test_set_inbound_copies_metadata(self):
        ctx = TurnContext()
        original = {"key": "val"}
        ctx.set_inbound("cli", "u", original)
        original["key"] = "changed"
        assert ctx.metadata["key"] == "val"

    def test_clear(self):
        ctx = TurnContext()
        ctx.set_inbound("gmail", "x@y.com", {"a": 1})
        ctx.sent_hashes.add("abc")
        ctx.pending_outbound.append(PendingOutbound(channel="cli", recipient=None, body="x"))
        ctx.clear()
        assert ctx.channel == "cli"
        assert ctx.sender is None
        assert ctx.metadata == {}
        assert ctx.sent_hashes == set()
        assert ctx.pending_outbound == []


class TestTurnReset:
    def test_set_inbound_resets_sent_hashes(self):
        ctx = TurnContext()
        ctx.sent_hashes.add("h1")
        ctx.set_inbound("cli", "u", {})
        assert ctx.sent_hashes == set()

    def test_set_inbound_resets_pending_outbound(self):
        ctx = TurnContext()
        ctx.pending_outbound.append(PendingOutbound(channel="cli", recipient=None, body="x"))
        ctx.set_inbound("cli", "u", {})
        assert ctx.pending_outbound == []
