"""Tests for Conversation mutation helpers."""

from datetime import datetime

from chat_agent.context import Conversation


def test_replace_messages_restores_history_without_callback():
    seen = []
    original = Conversation()
    original.add("user", "hi")
    original.add("assistant", "hello")

    restored = Conversation(on_message=seen.append)
    restored.replace_messages(original.get_messages())

    assert len(restored.get_messages()) == 2
    assert seen == []


def test_truncate_to_keeps_prefix_and_returns_removed_count():
    conversation = Conversation()
    conversation.add("user", "u1")
    conversation.add("assistant", "a1")
    conversation.add("user", "u2")

    removed = conversation.truncate_to(2)

    assert removed == 1
    assert [entry.content for entry in conversation.get_messages()] == ["u1", "a1"]


def test_truncate_to_noops_when_length_is_large_enough():
    conversation = Conversation()
    conversation.add("user", "u1")

    removed = conversation.truncate_to(5)

    assert removed == 0
    assert len(conversation.get_messages()) == 1


def test_set_on_message_updates_future_callback():
    seen = []
    conversation = Conversation()

    conversation.set_on_message(seen.append)
    conversation.add("user", "hello")

    assert len(seen) == 1
    assert seen[0].content == "hello"


def test_len_tracks_current_history_size():
    conversation = Conversation()
    assert len(conversation) == 0

    conversation.add("user", "first", timestamp=datetime(2024, 1, 2, 3, 4, 5))
    conversation.add("assistant", "second")
    assert len(conversation) == 2

    conversation.truncate_to(1)
    assert len(conversation) == 1
