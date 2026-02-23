"""UI sink abstraction used by runtime code to emit typed UI events."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

from .events import UiEvent


class UiSink(Protocol):
    """A write-only sink for typed UI events."""

    def emit(self, event: UiEvent) -> None:
        """Emit one UI event."""
        ...


@dataclass
class QueueUiSink:
    """Thread-safe in-memory sink for bridging worker threads to a UI loop."""

    _events: deque[UiEvent] = field(default_factory=deque)
    _lock: Lock = field(default_factory=Lock)
    _on_emit: Callable[[UiEvent], None] | None = None

    def emit(self, event: UiEvent) -> None:
        callback = self._on_emit
        if callback is not None:
            callback(event)
            return
        with self._lock:
            self._events.append(event)

    def drain(self) -> list[UiEvent]:
        with self._lock:
            items = list(self._events)
            self._events.clear()
        return items

    def set_on_emit(self, callback: Callable[[UiEvent], None] | None) -> None:
        """Set callback invoked after enqueue; used to wake the UI thread."""
        self._on_emit = callback
