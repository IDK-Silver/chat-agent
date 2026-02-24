"""Tests for get_channel_history tool (Discord backend v1)."""

import json

from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.discord_history import DiscordHistoryStore
from chat_agent.agent.turn_context import TurnContext
from chat_agent.tools.builtin.get_channel_history import (
    GET_CHANNEL_HISTORY_DEFINITION,
    create_get_channel_history,
)


def _make_tool(tmp_path):
    contact_map = ContactMap(tmp_path / "cache")
    history = DiscordHistoryStore(tmp_path / "cache")
    turn_context = TurnContext()
    fn = create_get_channel_history(history, contact_map, turn_context)
    return fn, history, contact_map, turn_context


class TestDefinition:
    def test_name(self):
        assert GET_CHANNEL_HISTORY_DEFINITION.name == "get_channel_history"

    def test_mentions_discord_only(self):
        desc = GET_CHANNEL_HISTORY_DEFINITION.description or ""
        assert "discord" in desc.lower()


class TestGetChannelHistory:
    def test_non_discord_rejected(self, tmp_path):
        fn, _, _, _ = _make_tool(tmp_path)
        result = fn(channel="gmail")
        assert "Error" in result
        assert "discord" in result.lower()

    def test_channel_id_takes_precedence(self, tmp_path):
        fn, history, contact_map, _ = _make_tool(tmp_path)
        contact_map.update("discord", "wrong-id", "#general @ Guild")
        history.append_message_create(
            channel_id="right-id",
            event={
                "event_time": "2026-02-24T10:00:00+00:00",
                "message_id": "m1",
                "message_time": "2026-02-24T10:00:00+00:00",
                "author_id": "u1",
                "author_name": "alice",
                "author_display_name": "Alice",
                "raw_content": "hello",
                "embeds": [],
                "stickers": [],
                "attachments": [],
                "normalized_text": "hello",
            },
        )

        payload = json.loads(
            fn(channel="discord", to="#general @ Guild", channel_id="right-id")
        )

        assert payload["channel_id"] == "right-id"
        assert payload["count"] == 1

    def test_default_to_current_discord_channel(self, tmp_path):
        fn, history, _, turn_context = _make_tool(tmp_path)
        history.append_message_create(
            channel_id="c1",
            event={
                "event_time": "2026-02-24T10:00:00+00:00",
                "message_id": "m1",
                "message_time": "2026-02-24T10:00:00+00:00",
                "author_id": "u1",
                "author_name": "alice",
                "author_display_name": "Alice",
                "raw_content": "hello",
                "embeds": [],
                "stickers": [],
                "attachments": [],
                "normalized_text": "hello",
            },
        )
        turn_context.set_inbound("discord", "#general @ Guild", {"channel_id": "c1"})

        payload = json.loads(fn(channel="discord"))
        assert payload["channel_id"] == "c1"
        assert payload["count"] == 1

    def test_dm_name_resolves_via_registry_peer_fallback(self, tmp_path):
        fn, history, contact_map, _ = _make_tool(tmp_path)
        contact_map.update("discord", "user-1", "Alice")
        history.upsert_channel(
            channel_id="dm-chan-1",
            guild_id=None,
            guild_name=None,
            channel_name="dm",
            alias="Alice",
            filter_mode="all",
            source="dm",
            extra={"dm_peer_user_id": "user-1"},
        )
        history.append_message_create(
            channel_id="dm-chan-1",
            event={
                "event_time": "2026-02-24T10:00:00+00:00",
                "message_id": "m1",
                "message_time": "2026-02-24T10:00:00+00:00",
                "author_id": "user-1",
                "author_name": "alice",
                "author_display_name": "Alice",
                "raw_content": "dm hello",
                "embeds": [],
                "stickers": [],
                "attachments": [],
                "normalized_text": "dm hello",
            },
        )

        payload = json.loads(fn(channel="discord", to="Alice"))
        assert payload["channel_id"] == "dm-chan-1"
        assert payload["count"] == 1
        assert payload["messages"][0]["content"] == "dm hello"

    def test_folds_edit_to_latest_with_flag(self, tmp_path):
        fn, history, _, _ = _make_tool(tmp_path)
        base = {
            "event_time": "2026-02-24T10:00:00+00:00",
            "message_id": "m1",
            "message_time": "2026-02-24T10:00:00+00:00",
            "author_id": "u1",
            "author_name": "alice",
            "author_display_name": "Alice",
            "embeds": [],
            "stickers": [],
            "attachments": [],
            "reply_to_message_id": None,
            "reply_to_author_id": None,
            "reply_to_author_name": None,
            "reply_to_preview_text": None,
        }
        history.append_message_create(
            channel_id="c1",
            event={**base, "raw_content": "old", "normalized_text": "old"},
        )
        history.append_message_edit(
            channel_id="c1",
            event={
                **base,
                "event_time": "2026-02-24T10:01:00+00:00",
                "edited_at": "2026-02-24T10:01:00+00:00",
                "raw_content": "new",
                "normalized_text": "new",
            },
        )

        payload = json.loads(fn(channel="discord", channel_id="c1"))
        msg = payload["messages"][0]
        assert msg["content"] == "new"
        assert msg["edited"] is True
        assert msg["edited_at"] is not None
