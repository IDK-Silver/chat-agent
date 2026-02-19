"""Mutable per-turn context for the send_message tool.

Created once at startup; updated by AgentCore._process_inbound()
before each run_turn call.  The send_message tool reads from this
to determine reply-mode metadata (channel, sender, thread info).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingOutbound:
    """Buffered outbound message for deferred console display."""

    channel: str
    recipient: str | None
    body: str


@dataclass
class TurnContext:
    """Holds current inbound message metadata for the active turn."""

    channel: str = "cli"
    sender: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sent_hashes: set[str] = field(default_factory=set)
    pending_outbound: list[PendingOutbound] = field(default_factory=list)

    def set_inbound(
        self,
        channel: str,
        sender: str | None,
        metadata: dict[str, Any],
    ) -> None:
        """Update context with current inbound message info."""
        self.channel = channel
        self.sender = sender
        self.metadata = dict(metadata)  # copy for mutation safety
        self.sent_hashes = set()
        self.pending_outbound = []

    def clear(self) -> None:
        """Reset to defaults after turn completes."""
        self.channel = "cli"
        self.sender = None
        self.metadata = {}
        self.sent_hashes = set()
        self.pending_outbound = []

    def check_sent_dedup(
        self, channel: str, to: str | None, body: str,
    ) -> bool:
        """Return True if this (channel, to, body) was already sent this turn.

        If not yet sent, records it and returns False.
        """
        key = hashlib.sha256(
            f"{channel}\0{to or ''}\0{body}".encode(),
        ).hexdigest()
        if key in self.sent_hashes:
            return True
        self.sent_hashes.add(key)
        return False
