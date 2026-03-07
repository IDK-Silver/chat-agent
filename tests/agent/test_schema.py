"""Tests for agent.schema message types."""

from datetime import datetime

from chat_agent.agent.schema import (
    InboundMessage,
    OutboundMessage,
    PendingOutbound,
    ReloadSentinel,
    ReloadSystemPromptSentinel,
    ShutdownSentinel,
)


def test_inbound_message_defaults():
    msg = InboundMessage(channel="cli", content="hello", priority=0, sender="u1")
    assert msg.channel == "cli"
    assert msg.content == "hello"
    assert msg.priority == 0
    assert msg.sender == "u1"
    assert msg.metadata == {}
    assert isinstance(msg.timestamp, datetime)


def test_inbound_message_with_metadata():
    msg = InboundMessage(
        channel="line",
        content="hi",
        priority=1,
        sender="friend",
        metadata={"reply_token": "abc"},
    )
    assert msg.metadata["reply_token"] == "abc"


def test_outbound_message():
    msg = OutboundMessage(channel="cli", content="world")
    assert msg.channel == "cli"
    assert msg.content == "world"
    assert msg.metadata == {}


def test_pending_outbound_defaults():
    out = OutboundMessage(channel="line", content="hey")
    pending = PendingOutbound(message=out)
    assert pending.retry_count == 0
    assert pending.max_retries == 3
    assert pending.next_retry is None


def test_shutdown_sentinel_default_graceful():
    s = ShutdownSentinel()
    assert s.graceful is True


def test_shutdown_sentinel_non_graceful():
    s = ShutdownSentinel(graceful=False)
    assert s.graceful is False


def test_reload_sentinel_constructs():
    assert isinstance(ReloadSentinel(), ReloadSentinel)


def test_reload_system_prompt_sentinel_constructs():
    assert isinstance(ReloadSystemPromptSentinel(), ReloadSystemPromptSentinel)
