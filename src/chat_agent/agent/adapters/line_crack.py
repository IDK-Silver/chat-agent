"""LINE Desktop macOS crack adapter: badge polling + Vision LLM + AppleScript.

This adapter automates LINE Desktop on macOS by:
- Detecting unread messages via dock badge (lsappinfo)
- Reading chat content by screenshotting and parsing with Vision LLM
- Sending messages via AppleScript UI automation

Named "crack" because it bypasses LINE's lack of public API.  May be
replaced by an official API adapter in the future.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

from ..contact_map import ContactMap
from ..schema import InboundMessage, OutboundMessage
from .formatting import markdown_to_plaintext

if TYPE_CHECKING:
    from ..core import AgentCore
    from ...llm.base import LLMClient

logger = logging.getLogger(__name__)

_BADGE_RE = re.compile(r'"label"="(\d+)"')

# ------------------------------------------------------------------
# AppleScript templates
# ------------------------------------------------------------------

_ACTIVATE = 'tell application "LINE" to activate'

_SWITCH_TO_CHAT = """\
tell application "System Events"
  tell process "LINE"
    tell menu bar 1
      tell menu bar item "顯示"
        click menu item "聊天" of menu 1
      end tell
    end tell
  end tell
end tell"""

_SEARCH_AND_OPEN = """\
tell application "System Events"
  tell process "LINE"
    tell splitter group 1 of window 1
      set focused of text field 1 to true
      set value of text field 1 to ""
    end tell
    delay 0.3
    set the clipboard to "{name}"
    keystroke "v" using command down
    delay 2
    key code 125
    delay 0.2
    key code 125
    delay 0.2
    key code 36
    delay 1.5
  end tell
end tell"""

_SEND_TEXT = """\
tell application "System Events"
  tell process "LINE"
    tell splitter group 1 of window 1
      set value of text field 1 to ""
      tell splitter group 1
        set value of text area 1 to "{text}"
        delay 0.3
        set focused of text area 1 to true
      end tell
    end tell
    delay 0.3
    key code 76
  end tell
end tell"""

_CLOSE_CHAT = """\
tell application "System Events"
  tell process "LINE"
    key code 53
  end tell
end tell"""

# ------------------------------------------------------------------
# Vision LLM prompts and schemas
# ------------------------------------------------------------------

_CHAT_LIST_SYSTEM = """\
You are a screenshot parser for LINE Desktop on macOS.
You see the chat list sidebar showing recent conversations.

Extract each visible chat row with:
- name: contact/group display name (exactly as shown)
- preview: last message snippet text
- unread_count: unread badge number (0 if no green badge visible)

Return ONLY chats with unread_count > 0. Empty array if none.
Output JSON matching the schema. No explanation."""

_CHAT_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "preview": {"type": "string"},
                    "unread_count": {"type": "integer"},
                },
                "required": ["name", "unread_count"],
            },
        },
    },
    "required": ["chats"],
}

_MESSAGES_SYSTEM = """\
You are a screenshot parser for LINE Desktop on macOS.
You see one or more screenshots of a chat conversation.

Extract each message bubble:
- role: "sent" (right-aligned, colored/green) or "received" (left-aligned, white/gray)
- sender: name label above received message groups (empty string if sent or not visible)
- text: full message text. Note stickers/images as [sticker] or [image].
- time: timestamp near the message (e.g. "14:30"), empty string if not visible

Order: oldest (top of first screenshot) to newest (bottom of last screenshot).
Skip date separators, read receipts, and system notices.
Deduplicate messages that appear in overlapping screenshots.
Output JSON matching the schema. No explanation."""

_MESSAGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chat_name": {"type": "string"},
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["sent", "received"]},
                    "sender": {"type": "string"},
                    "text": {"type": "string"},
                    "time": {"type": "string"},
                },
                "required": ["role", "text"],
            },
        },
    },
    "required": ["messages"],
}


# ------------------------------------------------------------------
# Low-level driver
# ------------------------------------------------------------------

class _LINEDesktopDriver:
    """Low-level LINE Desktop automation on macOS.

    All pyautogui operations use lazy imports so this module can be
    imported without a display.
    """

    def get_badge_count(self) -> int:
        """Check LINE unread badge via lsappinfo."""
        try:
            result = subprocess.run(
                ["lsappinfo", "info", "-only", "StatusLabel", "LINE"],
                capture_output=True, text=True, timeout=5,
            )
            match = _BADGE_RE.search(result.stdout)
            return int(match.group(1)) if match else 0
        except (subprocess.TimeoutExpired, OSError):
            return 0

    def activate(self) -> None:
        self._osascript(_ACTIVATE)
        time.sleep(0.5)

    def switch_to_chat_view(self) -> None:
        self._osascript(_SWITCH_TO_CHAT)
        time.sleep(0.5)

    def click_at(self, x: float, y: float) -> None:
        import pyautogui
        pyautogui.click(x, y)

    def scroll_down(self, cx: float, cy: float, amount: int = 10) -> None:
        """Scroll down in the chat area (see newer messages)."""
        from ...gui.actions import scroll_at_pixel
        scroll_at_pixel(cx, cy, "down", amount)

    def search_and_open(self, name: str) -> None:
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        self._osascript(_SEARCH_AND_OPEN.replace("{name}", escaped))
        time.sleep(0.5)

    def send_text(self, text: str) -> None:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        self._osascript(_SEND_TEXT.replace("{text}", escaped))
        time.sleep(0.3)

    def close_chat(self) -> None:
        self._osascript(_CLOSE_CHAT)
        time.sleep(0.3)

    def take_screenshot(
        self,
        *,
        region: tuple[int, int, int, int] | None = None,
        max_width: int | None = None,
        quality: int = 80,
    ) -> Any:
        """Capture a screenshot, returning a ContentPart."""
        from ...gui.actions import take_screenshot
        return take_screenshot(max_width=max_width, quality=quality, region=region)

    @staticmethod
    def _osascript(script: str, timeout: int = 10) -> str:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()


# ------------------------------------------------------------------
# Vision LLM parser
# ------------------------------------------------------------------

class _LINEVisionParser:
    """Parse LINE Desktop screenshots via Vision LLM."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def parse_chat_list(self, screenshot: Any) -> list[dict[str, Any]]:
        """Parse chat list screenshot into unread chat entries."""
        from ...llm.schema import ContentPart, Message
        from ...llm.json_extract import extract_json_object

        messages = [
            Message(role="system", content=_CHAT_LIST_SYSTEM),
            Message(role="user", content=[
                screenshot,
                ContentPart(type="text", text="Parse all unread chats from this LINE Desktop sidebar."),
            ]),
        ]
        raw = self._client.chat(messages, response_schema=_CHAT_LIST_SCHEMA)
        parsed = extract_json_object(raw)
        if parsed and "chats" in parsed:
            return [c for c in parsed["chats"] if c.get("unread_count", 0) > 0]
        return []

    def parse_messages(self, screenshots: list[Any]) -> tuple[str, list[dict[str, Any]]]:
        """Parse chat message screenshots. Returns (chat_name, messages)."""
        from ...llm.schema import ContentPart, Message

        if not screenshots:
            return "", []

        from ...llm.json_extract import extract_json_object

        content: list[ContentPart] = list(screenshots)
        content.append(ContentPart(
            type="text",
            text="Parse all messages in chronological order. Include the chat name.",
        ))
        messages = [
            Message(role="system", content=_MESSAGES_SYSTEM),
            Message(role="user", content=content),
        ]
        raw = self._client.chat(messages, response_schema=_MESSAGES_SCHEMA)
        parsed = extract_json_object(raw)
        if parsed and "messages" in parsed:
            chat_name = parsed.get("chat_name", "")
            return chat_name, parsed["messages"]
        return "", []


# ------------------------------------------------------------------
# Screenshot comparison
# ------------------------------------------------------------------

def _image_similarity(data_a: bytes, data_b: bytes) -> float:
    """Compare two JPEG byte buffers. Returns similarity ratio 0-1."""
    from PIL import Image, ImageChops

    img_a = Image.open(io.BytesIO(data_a))
    img_b = Image.open(io.BytesIO(data_b))
    if img_a.size != img_b.size:
        return 0.0
    diff = ImageChops.difference(img_a, img_b)
    raw = diff.tobytes()
    total = sum(raw)
    max_total = len(raw) * 255
    return 1.0 - (total / max_total) if max_total else 1.0


# ------------------------------------------------------------------
# Adapter
# ------------------------------------------------------------------

class LineCrackAdapter:
    """LINE Desktop macOS channel adapter.

    Polls for unread messages via dock badge, reads messages using
    Vision LLM on screenshots, sends via AppleScript UI automation.
    Shares ``gui_lock`` with gui_task to avoid concurrent GUI access.
    """

    channel_name = "line"
    priority = 1

    def __init__(
        self,
        *,
        gui_lock: threading.Lock,
        vision_client: LLMClient,
        contact_map: ContactMap,
        poll_interval: int = 30,
        screenshot_max_width: int | None = 1280,
        screenshot_quality: int = 80,
        scroll_similarity_threshold: float = 0.995,
        max_scroll_captures: int = 20,
        driver: _LINEDesktopDriver | None = None,
    ) -> None:
        self._gui_lock = gui_lock
        self._vision = _LINEVisionParser(vision_client)
        self._contact_map = contact_map
        self._poll_interval = poll_interval
        self._ss_max_width = screenshot_max_width
        self._ss_quality = screenshot_quality
        self._scroll_threshold = scroll_similarity_threshold
        self._max_scroll = max_scroll_captures
        self._driver = driver or _LINEDesktopDriver()

        self._agent: AgentCore | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- ChannelAdapter protocol --------------------------------------

    def start(self, agent: AgentCore) -> None:
        self._agent = agent
        self._thread = threading.Thread(
            target=self._poll_loop, name="line-crack-poll", daemon=True,
        )
        self._thread.start()

    def send(self, message: OutboundMessage) -> None:
        recipient = message.metadata.get("reply_to")
        if not recipient:
            logger.warning("LINE send: no reply_to in metadata, skipping")
            return
        body = markdown_to_plaintext(message.content)
        with self._gui_lock:
            try:
                self._send_message(recipient, body)
            except Exception:
                logger.exception("LINE send failed to %s", recipient)

    def on_turn_start(self, channel: str) -> None:
        pass

    def on_turn_complete(self) -> None:
        pass

    def stop(self) -> None:
        self._stop_event.set()

    # -- Polling ------------------------------------------------------

    def _poll_loop(self) -> None:
        assert self._agent is not None
        while not self._stop_event.is_set():
            try:
                badge = self._driver.get_badge_count()
                if badge > 0:
                    logger.info("LINE badge: %d unread", badge)
                    with self._gui_lock:
                        self._process_unread()
            except Exception:
                logger.exception("LINE poll error")
            self._stop_event.wait(self._poll_interval)

    def _process_unread(self) -> None:
        """Full unread processing cycle (must hold gui_lock)."""
        self._driver.activate()
        self._driver.switch_to_chat_view()

        # Screenshot the chat list (left panel)
        list_ss = self._driver.take_screenshot(
            max_width=self._ss_max_width,
            quality=self._ss_quality,
        )

        # Parse with Vision LLM
        unread_chats = self._vision.parse_chat_list(list_ss)
        if not unread_chats:
            logger.debug("LINE: Vision LLM found no unread chats")
            return

        # Process each unread chat
        for chat_info in unread_chats:
            try:
                self._process_single_chat(chat_info)
            except Exception:
                logger.exception(
                    "LINE: failed to process chat %s", chat_info.get("name"),
                )
            finally:
                try:
                    self._driver.close_chat()
                except Exception:
                    pass
                time.sleep(0.3)

    def _process_single_chat(self, chat_info: dict[str, Any]) -> None:
        """Open one chat, capture messages, enqueue."""
        assert self._agent is not None
        import pyautogui

        name = chat_info.get("name", "")
        screen_w, screen_h = pyautogui.size()

        # Click the first row in the chat list (unread is always at top)
        # Left panel is roughly the first ~40% of the window
        row_x = screen_w * 0.15
        # First visible row is approximately 20% from top
        row_y = screen_h * 0.20
        self._driver.click_at(row_x, row_y)
        time.sleep(1.0)

        # Capture all messages by scrolling
        screenshots = self._capture_messages(screen_w, screen_h)
        if not screenshots:
            logger.warning("LINE: no screenshots captured for %s", name)
            return

        # Parse with Vision LLM
        chat_name, messages = self._vision.parse_messages(screenshots)
        display_name = chat_name or name

        # Filter received messages
        received = [m for m in messages if m.get("role") == "received"]
        if not received:
            logger.debug("LINE: no received messages in %s", display_name)
            return

        # Build content
        content_parts = []
        for msg in received:
            text = msg.get("text", "")
            sender = msg.get("sender", "")
            t = msg.get("time", "")
            if sender and t:
                content_parts.append(f"[{sender} {t}] {text}")
            elif sender:
                content_parts.append(f"[{sender}] {text}")
            elif t:
                content_parts.append(f"[{t}] {text}")
            else:
                content_parts.append(text)
        content = "\n".join(content_parts)

        # Update contact map
        self._contact_map.update("line", display_name, display_name)

        # Enqueue
        inbound = InboundMessage(
            channel="line",
            content=content,
            priority=self.priority,
            sender=display_name,
            metadata={"reply_to": display_name},
        )
        self._agent.enqueue(inbound)
        logger.info("LINE: enqueued message from %s", display_name)

    def _capture_messages(
        self, screen_w: float, screen_h: float,
    ) -> list[Any]:
        """Scroll through chat and capture screenshots until no change."""
        # Chat area center (right ~60% of screen)
        chat_cx = screen_w * 0.65
        chat_cy = screen_h * 0.45

        screenshots: list[Any] = []
        prev_bytes: bytes | None = None

        for _ in range(self._max_scroll):
            ss = self._driver.take_screenshot(
                max_width=self._ss_max_width,
                quality=self._ss_quality,
            )
            current_bytes = base64.b64decode(ss.data)

            if prev_bytes is not None:
                sim = _image_similarity(prev_bytes, current_bytes)
                if sim >= self._scroll_threshold:
                    break

            screenshots.append(ss)
            prev_bytes = current_bytes

            # Scroll down to see more messages
            self._driver.scroll_down(chat_cx, chat_cy, amount=10)
            time.sleep(0.8)

        return screenshots

    # -- Sending ------------------------------------------------------

    def _send_message(self, recipient: str, body: str) -> None:
        """Search for contact, open chat, type, send, close."""
        self._driver.activate()
        self._driver.switch_to_chat_view()
        time.sleep(0.3)

        self._driver.search_and_open(recipient)
        time.sleep(0.5)

        self._driver.send_text(body)
        time.sleep(0.3)

        self._driver.close_chat()
        logger.info("LINE: message sent to %s", recipient)
