"""Web Chat channel adapter."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..schema import InboundMessage, OutboundMessage
from ..web_chat import WebChatEvent, WebChatStore

if TYPE_CHECKING:
    from ..core import AgentCore


class WebChatUnavailable(RuntimeError):
    """Raised when the Web Chat adapter cannot accept messages."""


class WebAdapter:
    """Local Web Chat adapter backed by a durable event log."""

    channel_name = "web"
    priority = 0

    def __init__(self, *, events_path: Path, history_limit: int = 200) -> None:
        self.store = WebChatStore(events_path)
        self.history_limit = history_limit
        self._agent: AgentCore | None = None
        self._lock = threading.Lock()
        self._processing_web = False
        self._processing_request_id: str | None = None

    def start(self, agent: AgentCore) -> None:
        self._agent = agent

    def send(self, message: OutboundMessage) -> None:
        request_id = _metadata_request_id(message.metadata)
        self.store.append_event(
            kind="message",
            role="assistant",
            content=message.content,
            request_id=request_id,
        )

    def on_turn_start(self, channel: str) -> None:
        if channel != self.channel_name:
            return
        request_id = self._current_request_id()
        with self._lock:
            self._processing_web = True
            self._processing_request_id = request_id
        self.store.append_event(
            kind="status",
            role="system",
            status="processing",
            request_id=request_id,
        )

    def on_turn_complete(self) -> None:
        with self._lock:
            if not self._processing_web:
                return
            request_id = self._processing_request_id
            self._processing_web = False
            self._processing_request_id = None
        self.store.append_event(
            kind="status",
            role="system",
            status="idle",
            request_id=request_id,
        )

    def stop(self) -> None:
        with self._lock:
            was_processing = self._processing_web
            request_id = self._processing_request_id
            self._processing_web = False
            self._processing_request_id = None
        if was_processing:
            self.store.append_event(
                kind="status",
                role="system",
                status="idle",
                request_id=request_id,
            )

    def submit_message(self, content: str) -> WebChatEvent:
        """Append the user event and enqueue it for AgentCore."""
        text = content.strip()
        if not text:
            raise ValueError("content is required")
        if self._agent is None:
            raise WebChatUnavailable("web chat adapter is not ready")

        request_id = uuid.uuid4().hex
        event = self.store.append_event(
            kind="message",
            role="user",
            content=text,
            request_id=request_id,
        )
        self.store.append_event(
            kind="status",
            role="system",
            status="queued",
            request_id=request_id,
        )
        try:
            self._agent.enqueue(
                InboundMessage(
                    channel=self.channel_name,
                    content=text,
                    priority=self.priority,
                    sender="web",
                    metadata={
                        "source": "web_chat",
                        "web_request_id": request_id,
                    },
                )
            )
        except Exception as exc:
            self.store.append_event(
                kind="error",
                role="system",
                content=str(exc),
                status="error",
                request_id=request_id,
            )
            raise RuntimeError(str(exc)) from exc
        return event

    def _current_request_id(self) -> str | None:
        if self._agent is None or self._agent.turn_context is None:
            return None
        return _metadata_request_id(self._agent.turn_context.metadata)


def _metadata_request_id(metadata: dict[str, object]) -> str | None:
    value = metadata.get("web_request_id")
    return value if isinstance(value, str) and value else None
