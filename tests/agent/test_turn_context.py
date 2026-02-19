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


class TestSentDedup:
    def test_first_send_returns_false(self):
        ctx = TurnContext()
        assert ctx.check_sent_dedup("cli", None, "hi") is False

    def test_duplicate_returns_true(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("cli", None, "hi")
        assert ctx.check_sent_dedup("cli", None, "hi") is True

    def test_different_body_returns_false(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("cli", None, "hi")
        assert ctx.check_sent_dedup("cli", None, "bye") is False

    def test_different_channel_returns_false(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("cli", None, "hi")
        assert ctx.check_sent_dedup("gmail", None, "hi") is False

    def test_different_recipient_returns_false(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("gmail", "alice", "hi")
        assert ctx.check_sent_dedup("gmail", "bob", "hi") is False

    def test_set_inbound_clears(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("cli", None, "hi")
        ctx.set_inbound("cli", "u", {})
        assert ctx.check_sent_dedup("cli", None, "hi") is False

    def test_clear_clears(self):
        ctx = TurnContext()
        ctx.check_sent_dedup("cli", None, "hi")
        ctx.clear()
        assert ctx.check_sent_dedup("cli", None, "hi") is False
