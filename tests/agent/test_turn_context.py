"""Tests for TurnContext."""

from chat_agent.agent.turn_context import TurnContext


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

    def test_set_inbound_copies_metadata(self):
        ctx = TurnContext()
        original = {"key": "val"}
        ctx.set_inbound("cli", "u", original)
        original["key"] = "changed"
        assert ctx.metadata["key"] == "val"

    def test_clear(self):
        ctx = TurnContext()
        ctx.set_inbound("gmail", "x@y.com", {"a": 1})
        ctx.clear()
        assert ctx.channel == "cli"
        assert ctx.sender is None
        assert ctx.metadata == {}
