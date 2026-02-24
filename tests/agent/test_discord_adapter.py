"""Tests for Discord adapter behavior without a real gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.adapters.discord import DiscordAdapter
from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.discord_history import DiscordHistoryStore
from chat_agent.agent.schema import OutboundMessage
from chat_agent.core.schema import DiscordChannelConfig


class _FakeAgent:
    def __init__(self):
        self.enqueued = []
        self.turn_context = None

    def enqueue(self, msg):
        self.enqueued.append(msg)


@dataclass
class _FakeGuild:
    id: int
    name: str


@dataclass
class _FakeUser:
    id: int
    name: str
    display_name: str | None = None
    global_name: str | None = None

    async def create_dm(self):
        return _FakeSendChannel(self.id, "dm")


class _FakeAttachment:
    def __init__(self, filename="img.png", content_type="image/png", data=b"img", url="https://x/y"):
        self.filename = filename
        self.content_type = content_type
        self.size = len(data)
        self.url = url
        self._data = data

    async def read(self):
        return self._data


class _FakeReference:
    def __init__(self, message_id=None, resolved=None):
        self.message_id = message_id
        self.resolved = resolved


class _FakeMessage:
    def __init__(
        self,
        *,
        message_id: int,
        channel,
        author,
        content: str,
        mentions=None,
        attachments=None,
        created_at=None,
        embeds=None,
        stickers=None,
        reference=None,
    ):
        self.id = message_id
        self.channel = channel
        self.author = author
        self.content = content
        self.mentions = mentions or []
        self.attachments = attachments or []
        self.created_at = created_at or datetime.now(timezone.utc)
        self.embeds = embeds or []
        self.stickers = stickers or []
        self.reference = reference


class _FakeInboundChannel:
    def __init__(self, channel_id: int, name: str, guild: _FakeGuild | None):
        self.id = channel_id
        self.name = name
        self.guild = guild


class _FakeSendChannel:
    def __init__(self, channel_id: int, name: str, guild: _FakeGuild | None = None):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))

    def get_partial_message(self, message_id: int):
        return SimpleNamespace(id=message_id)

    async def trigger_typing(self):
        return None


def _make_adapter(tmp_path, **cfg_overrides):
    cfg = DiscordChannelConfig(**cfg_overrides)
    contact_map = ContactMap(tmp_path / "cache")
    history = DiscordHistoryStore(tmp_path / "cache")
    adapter = DiscordAdapter(
        token="token",
        contact_map=contact_map,
        thread_registry=None,
        config=cfg,
        history_store=history,
    )
    return adapter, contact_map, history


@pytest.mark.asyncio
class TestDiscordAdapterIngest:
    async def test_dm_respects_listen_dms_false(self, tmp_path):
        adapter, _, history = _make_adapter(tmp_path, listen_dms=False)
        adapter._agent = _FakeAgent()
        adapter._loop = asyncio.get_running_loop()
        adapter._self_user_id = "999"

        ch = _FakeInboundChannel(1, "dm", None)
        msg = _FakeMessage(
            message_id=10,
            channel=ch,
            author=_FakeUser(id=1, name="alice", display_name="Alice"),
            content="hi",
        )
        await adapter._handle_message(msg)

        assert history.read_events("1") == []
        assert adapter._agent.enqueued == []

    async def test_guild_mention_auto_registers_and_flushes_review(self, tmp_path):
        adapter, _, history = _make_adapter(tmp_path)
        adapter._agent = _FakeAgent()
        adapter._loop = asyncio.get_running_loop()
        adapter._self_user_id = "999"

        guild = _FakeGuild(7, "MyGuild")
        ch = _FakeInboundChannel(123, "general", guild)
        author = _FakeUser(id=1, name="alice", display_name="Alice")
        self_user = _FakeUser(id=999, name="agent")
        msg = _FakeMessage(
            message_id=10,
            channel=ch,
            author=author,
            content="@agent help",
            mentions=[self_user],
        )
        await adapter._handle_message(msg)

        entry = history.get_channel_entry("123")
        assert entry is not None
        assert entry["filter"] == "all"
        assert entry["alias"] == "#general @ MyGuild"
        # Mention review is debounce-triggered; flush directly for deterministic test.
        adapter._flush_mention_review("123")
        assert len(adapter._agent.enqueued) == 1
        inbound = adapter._agent.enqueued[0]
        assert inbound.channel == "discord"
        assert inbound.sender == "#general @ MyGuild"
        assert inbound.metadata["source"] == "guild_mention_review"

    async def test_hard_allowlist_blocks_unlisted_channel(self, tmp_path):
        adapter, _, history = _make_adapter(
            tmp_path,
            listen_channels=[{"channel_id": "777", "filter": "mention_only"}],
        )
        adapter._agent = _FakeAgent()
        adapter._loop = asyncio.get_running_loop()
        adapter._self_user_id = "999"
        adapter._hard_allowlist_filters = {"777": "mention_only"}

        guild = _FakeGuild(7, "MyGuild")
        ch = _FakeInboundChannel(123, "general", guild)
        self_user = _FakeUser(id=999, name="agent")
        msg = _FakeMessage(
            message_id=10,
            channel=ch,
            author=_FakeUser(id=1, name="alice", display_name="Alice"),
            content="@agent help",
            mentions=[self_user],
        )
        await adapter._handle_message(msg)
        assert history.get_channel_entry("123") is None

    async def test_dm_typing_resets_timer(self, tmp_path):
        adapter, _, _ = _make_adapter(tmp_path)
        adapter._agent = _FakeAgent()
        adapter._loop = asyncio.get_running_loop()
        adapter._self_user_id = "999"

        ch = _FakeInboundChannel(1, "dm", None)
        msg = _FakeMessage(
            message_id=10,
            channel=ch,
            author=_FakeUser(id=1, name="alice", display_name="Alice"),
            content="hi",
        )
        await adapter._handle_message(msg)
        old_handle = adapter._timers["dm:1"]
        adapter._handle_typing("1", "1")
        new_handle = adapter._timers["dm:1"]
        assert new_handle is not old_handle
        old_handle.cancel()
        new_handle.cancel()
        adapter._timers.clear()

    async def test_periodic_review_enqueues_batch_for_filter_all(self, tmp_path):
        adapter, _, history = _make_adapter(tmp_path)
        adapter._agent = _FakeAgent()
        adapter._loop = asyncio.get_running_loop()
        adapter._self_user_id = "999"
        history.upsert_channel(
            channel_id="123",
            guild_id="7",
            guild_name="MyGuild",
            channel_name="general",
            alias="#general @ MyGuild",
            filter_mode="all",
            source="auto_mention",
            review_interval_seconds=1,
        )
        history.append_message_create(
            channel_id="123",
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
                "reply_to_message_id": None,
                "reply_to_author_id": None,
                "reply_to_author_name": None,
                "reply_to_preview_text": None,
            },
        )
        adapter._run_periodic_review_tick()
        assert len(adapter._agent.enqueued) == 1
        assert adapter._agent.enqueued[0].metadata["source"] == "guild_review"


@pytest.mark.asyncio
class TestDiscordAdapterSend:
    async def test_async_send_splits_and_references(self, tmp_path):
        adapter, _, _ = _make_adapter(tmp_path, send_delay_min=0, send_delay_max=0)
        send_channel = _FakeSendChannel(123, "general", _FakeGuild(7, "Guild"))
        fake_client = SimpleNamespace(
            get_channel=lambda cid: send_channel if cid == 123 else None,
            fetch_channel=None,
        )
        adapter._client = fake_client

        body = "a" * 2100
        await adapter._async_send(
            OutboundMessage(
                channel="discord",
                content=body,
                metadata={"channel_id": "123", "message_id": "456"},
            )
        )

        assert len(send_channel.sent) == 2
        first_kwargs = send_channel.sent[0][1]
        assert "reference" in first_kwargs
        assert first_kwargs["reference"].id == 456

    async def test_thinking_typing_hooks_schedule(self, tmp_path):
        adapter, _, _ = _make_adapter(tmp_path)
        adapter._agent = _FakeAgent()
        adapter._agent.turn_context = SimpleNamespace(metadata={"channel_id": "123"})
        adapter._loop = MagicMock()
        adapter._loop_ready.set()
        def _fake_submit(coro, loop):
            del loop
            coro.close()
            return MagicMock()

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_fake_submit) as run_coro:
            adapter.on_turn_start("discord")
            adapter.on_turn_complete()
        assert run_coro.call_count == 2
