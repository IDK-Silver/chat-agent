"""Persistent priority queue backed by filesystem.

Storage layout:
    {queue_dir}/pending/   - messages waiting to be processed (one JSON file each)
    {queue_dir}/active/    - message currently being processed (moved from pending/)

On startup, any files left in active/ are moved back to pending/ (crash recovery).
Processed messages are deleted (ack).
"""

import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import InboundMessage, RefreshSentinel, ShutdownSentinel

logger = logging.getLogger(__name__)


def _serialize(msg: InboundMessage) -> dict[str, Any]:
    return {
        "channel": msg.channel,
        "content": msg.content,
        "priority": msg.priority,
        "sender": msg.sender,
        "metadata": msg.metadata,
        "timestamp": msg.timestamp.isoformat(),
    }


def _deserialize(data: dict[str, Any]) -> InboundMessage:
    return InboundMessage(
        channel=data["channel"],
        content=data["content"],
        priority=data["priority"],
        sender=data["sender"],
        metadata=data.get("metadata", {}),
        timestamp=datetime.fromisoformat(data["timestamp"]),
    )


class PersistentPriorityQueue:
    """Disk-backed priority queue.

    Uses an in-memory ``queue.PriorityQueue`` for fast blocking ``get()``
    and the filesystem for durability across process restarts.

    Thread-safe: multiple threads may call ``put()`` concurrently.
    Only one thread should call ``get()`` (the agent main loop).
    """

    def __init__(
        self,
        queue_dir: Path,
        *,
        discard_channels: set[str] | None = None,
    ) -> None:
        self._pending_dir = queue_dir / "pending"
        self._active_dir = queue_dir / "active"
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._active_dir.mkdir(parents=True, exist_ok=True)
        self._mem: queue.PriorityQueue[
            tuple[int, int, InboundMessage | ShutdownSentinel | RefreshSentinel, Path | None]
        ] = queue.PriorityQueue()
        self._seq: int = 0
        self._lock = threading.Lock()
        self._recover(discard_channels or set())

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def _recover(self, discard_channels: set[str]) -> None:
        """Move active -> pending, then load all pending into memory queue."""
        recovered = 0
        for f in sorted(self._active_dir.iterdir()):
            if f.suffix != ".json":
                continue
            f.rename(self._pending_dir / f.name)
            recovered += 1
        if recovered:
            logger.info("Recovered %d in-flight message(s) from last run", recovered)

        discarded = 0
        loaded = 0
        for f in sorted(self._pending_dir.iterdir()):
            if f.suffix != ".json":
                continue
            try:
                msg = _deserialize(json.loads(f.read_text()))
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Skipping corrupt queue file: %s", f.name)
                f.unlink()
                continue
            if msg.channel in discard_channels:
                f.unlink()
                discarded += 1
                continue
            self._seq += 1
            self._mem.put((msg.priority, self._seq, msg, f))
            loaded += 1

        if loaded:
            logger.info("Loaded %d pending message(s) from disk", loaded)
        if discarded:
            logger.info("Discarded %d stale message(s)", discarded)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(self, msg: InboundMessage | ShutdownSentinel | RefreshSentinel) -> None:
        """Enqueue a message.

        ``InboundMessage`` is persisted to disk.
        ``ShutdownSentinel`` and ``RefreshSentinel`` are transient (in-memory only).
        """
        with self._lock:
            self._seq += 1
            if isinstance(msg, ShutdownSentinel):
                # Priority -1 so shutdown is processed before any real message
                self._mem.put((-1, self._seq, msg, None))
                return
            if isinstance(msg, RefreshSentinel):
                # Lowest priority so real messages are always processed first
                self._mem.put((999, self._seq, msg, None))
                return
            filename = f"{msg.priority:04d}_{self._seq:08d}.json"
            filepath = self._pending_dir / filename
            filepath.write_text(json.dumps(_serialize(msg)))
            self._mem.put((msg.priority, self._seq, msg, filepath))

    def get(self) -> tuple[InboundMessage | ShutdownSentinel | RefreshSentinel, Path | None]:
        """Block until a message is available.

        Returns ``(message, receipt)``.  Pass *receipt* to ``ack()`` after
        the message has been fully processed.
        """
        _, _, msg, filepath = self._mem.get()  # blocks
        if filepath is not None:
            active_path = self._active_dir / filepath.name
            try:
                filepath.rename(active_path)
            except FileNotFoundError:
                # File already moved or deleted externally
                active_path = None
            return msg, active_path
        return msg, None

    def ack(self, receipt: Path | None) -> None:
        """Mark a message as processed (delete from disk)."""
        if receipt is not None:
            receipt.unlink(missing_ok=True)

    def pending_count(self) -> int:
        """Number of messages waiting (approximate, for diagnostics)."""
        return self._mem.qsize()
