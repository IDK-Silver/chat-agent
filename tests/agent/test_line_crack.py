"""Tests for agent.adapters.line_crack adapter."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from chat_agent.agent.adapters.line_crack import (
    LineCrackAdapter,
    _LINEDesktopDriver,
    _LINEVisionParser,
    _image_similarity,
    _BADGE_RE,
)
from chat_agent.agent.contact_map import ContactMap
from chat_agent.agent.schema import InboundMessage, OutboundMessage


# ------------------------------------------------------------------
# Badge parsing
# ------------------------------------------------------------------

class TestBadgeRegex:
    def test_parse_badge_count(self):
        output = '"StatusLabel"={ "label"="3" }'
        match = _BADGE_RE.search(output)
        assert match and match.group(1) == "3"

    def test_no_badge(self):
        output = '"StatusLabel"=(null)'
        assert _BADGE_RE.search(output) is None

    def test_large_badge(self):
        output = '"StatusLabel"={ "label"="142" }'
        match = _BADGE_RE.search(output)
        assert match and match.group(1) == "142"


# ------------------------------------------------------------------
# Driver
# ------------------------------------------------------------------

class TestLINEDesktopDriver:
    def test_get_badge_count_with_badge(self):
        driver = _LINEDesktopDriver()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='"StatusLabel"={ "label"="5" }',
            )
            assert driver.get_badge_count() == 5

    def test_get_badge_count_no_badge(self):
        driver = _LINEDesktopDriver()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout='"StatusLabel"=(null)')
            assert driver.get_badge_count() == 0

    def test_get_badge_count_timeout(self):
        import subprocess
        driver = _LINEDesktopDriver()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            assert driver.get_badge_count() == 0

    def test_activate(self):
        driver = _LINEDesktopDriver()
        with patch("subprocess.run") as mock_run, \
             patch("time.sleep"):
            mock_run.return_value = MagicMock(stdout="")
            driver.activate()
            assert mock_run.called

    def test_close_chat(self):
        driver = _LINEDesktopDriver()
        with patch("subprocess.run") as mock_run, \
             patch("time.sleep"):
            mock_run.return_value = MagicMock(stdout="")
            driver.close_chat()
            assert mock_run.called


# ------------------------------------------------------------------
# Image similarity
# ------------------------------------------------------------------

class TestImageSimilarity:
    def test_identical_images(self):
        from PIL import Image
        import io

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        data = buf.getvalue()
        assert _image_similarity(data, data) == 1.0

    def test_different_images(self):
        from PIL import Image
        import io

        img_a = Image.new("RGB", (10, 10), color="red")
        img_b = Image.new("RGB", (10, 10), color="blue")
        buf_a, buf_b = io.BytesIO(), io.BytesIO()
        img_a.save(buf_a, format="JPEG")
        img_b.save(buf_b, format="JPEG")
        sim = _image_similarity(buf_a.getvalue(), buf_b.getvalue())
        assert sim < 0.9

    def test_different_sizes(self):
        from PIL import Image
        import io

        img_a = Image.new("RGB", (10, 10), color="red")
        img_b = Image.new("RGB", (20, 20), color="red")
        buf_a, buf_b = io.BytesIO(), io.BytesIO()
        img_a.save(buf_a, format="JPEG")
        img_b.save(buf_b, format="JPEG")
        assert _image_similarity(buf_a.getvalue(), buf_b.getvalue()) == 0.0


# ------------------------------------------------------------------
# Vision parser
# ------------------------------------------------------------------

def _fake_screenshot():
    """Create a minimal ContentPart-like object for tests."""
    from chat_agent.llm.schema import ContentPart
    return ContentPart(type="image", media_type="image/jpeg", data="AAAA")


class TestLINEVisionParser:
    def _make_parser(self, llm_response: str) -> _LINEVisionParser:
        client = MagicMock()
        client.chat.return_value = llm_response
        return _LINEVisionParser(client)

    def test_parse_chat_list_with_unread(self):
        raw = '{"chats": [{"name": "Alice", "preview": "hi", "unread_count": 2}]}'
        parser = self._make_parser(raw)
        result = parser.parse_chat_list(_fake_screenshot())
        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert result[0]["unread_count"] == 2

    def test_parse_chat_list_filters_zero_unread(self):
        raw = '{"chats": [{"name": "Bob", "unread_count": 0}]}'
        parser = self._make_parser(raw)
        result = parser.parse_chat_list(_fake_screenshot())
        assert result == []

    def test_parse_chat_list_empty(self):
        parser = self._make_parser('{"chats": []}')
        result = parser.parse_chat_list(_fake_screenshot())
        assert result == []

    def test_parse_messages(self):
        raw = '{"chat_name": "Alice", "messages": [{"role": "received", "text": "hello", "sender": "Alice", "time": "14:30"}]}'
        parser = self._make_parser(raw)
        chat_name, msgs = parser.parse_messages([_fake_screenshot()])
        assert chat_name == "Alice"
        assert len(msgs) == 1
        assert msgs[0]["role"] == "received"
        assert msgs[0]["text"] == "hello"

    def test_parse_messages_empty(self):
        parser = self._make_parser('{"messages": []}')
        _, msgs = parser.parse_messages([_fake_screenshot()])
        assert msgs == []

    def test_parse_messages_no_screenshots(self):
        parser = self._make_parser("")
        _, msgs = parser.parse_messages([])
        assert msgs == []


# ------------------------------------------------------------------
# Adapter protocol
# ------------------------------------------------------------------

class _FakeAgent:
    """Minimal stand-in for AgentCore."""

    def __init__(self):
        self.enqueued: list[InboundMessage] = []

    def enqueue(self, msg):
        self.enqueued.append(msg)


class TestLineCrackAdapterProtocol:
    def _make_adapter(self, **kwargs) -> LineCrackAdapter:
        return LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            **kwargs,
        )

    def test_channel_name(self):
        adapter = self._make_adapter()
        assert adapter.channel_name == "line"

    def test_priority(self):
        adapter = self._make_adapter()
        assert adapter.priority == 1

    def test_start_creates_thread(self):
        adapter = self._make_adapter()
        agent = _FakeAgent()
        # Patch _poll_loop to avoid actual polling
        with patch.object(adapter, "_poll_loop"):
            adapter.start(agent)
            assert adapter._thread is not None
            assert adapter._thread.daemon is True
            adapter.stop()

    def test_stop_sets_event(self):
        adapter = self._make_adapter()
        adapter.stop()
        assert adapter._stop_event.is_set()

    def test_on_turn_start_noop(self):
        adapter = self._make_adapter()
        adapter.on_turn_start("cli")  # should not raise

    def test_on_turn_complete_noop(self):
        adapter = self._make_adapter()
        adapter.on_turn_complete()  # should not raise


# ------------------------------------------------------------------
# Send flow
# ------------------------------------------------------------------

class TestLineCrackSend:
    def test_send_calls_driver(self):
        driver = MagicMock(spec=_LINEDesktopDriver)
        adapter = LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            driver=driver,
        )
        msg = OutboundMessage(
            channel="line",
            content="Hello from agent",
            metadata={"reply_to": "Alice"},
        )
        adapter.send(msg)
        driver.activate.assert_called_once()
        driver.send_text.assert_called_once()
        driver.close_chat.assert_called_once()

    def test_send_no_reply_to_skips(self):
        driver = MagicMock(spec=_LINEDesktopDriver)
        adapter = LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            driver=driver,
        )
        msg = OutboundMessage(channel="line", content="Hi", metadata={})
        adapter.send(msg)
        driver.activate.assert_not_called()

    def test_send_strips_markdown(self):
        driver = MagicMock(spec=_LINEDesktopDriver)
        adapter = LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            driver=driver,
        )
        msg = OutboundMessage(
            channel="line",
            content="**bold** text",
            metadata={"reply_to": "Alice"},
        )
        adapter.send(msg)
        # markdown_to_plaintext should strip **
        call_args = driver.send_text.call_args[0][0]
        assert "**" not in call_args

    def test_send_error_does_not_raise(self):
        driver = MagicMock(spec=_LINEDesktopDriver)
        driver.activate.side_effect = RuntimeError("oops")
        adapter = LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            driver=driver,
        )
        msg = OutboundMessage(
            channel="line", content="Hi", metadata={"reply_to": "Bob"},
        )
        adapter.send(msg)  # should not raise


# ------------------------------------------------------------------
# Poll flow
# ------------------------------------------------------------------

class TestLineCrackPoll:
    def test_badge_zero_skips_processing(self):
        driver = MagicMock(spec=_LINEDesktopDriver)
        driver.get_badge_count.return_value = 0
        adapter = LineCrackAdapter(
            gui_lock=threading.Lock(),
            vision_client=MagicMock(),
            contact_map=MagicMock(spec=ContactMap),
            driver=driver,
        )
        agent = _FakeAgent()
        adapter._agent = agent
        # Simulate one poll iteration
        adapter._stop_event.set()  # stop after one check
        adapter._poll_loop()
        driver.activate.assert_not_called()
        assert len(agent.enqueued) == 0


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------

class TestLineCrackConfig:
    def test_default_config(self):
        from chat_agent.core.schema import LineCrackChannelConfig
        cfg = LineCrackChannelConfig()
        assert cfg.enabled is False
        assert cfg.poll_interval == 30
        assert cfg.screenshot_max_width == 1280
        assert cfg.screenshot_quality == 80
        assert cfg.scroll_similarity_threshold == 0.995
        assert cfg.max_scroll_captures == 20

    def test_config_in_channels(self):
        from chat_agent.core.schema import ChannelsConfig
        cfg = ChannelsConfig()
        assert hasattr(cfg, "line_crack")
        assert cfg.line_crack.enabled is False
