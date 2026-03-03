"""Mutable per-turn context for the send_message tool.

Created once at startup; updated by AgentCore._process_inbound()
before each run_turn call.  The send_message tool reads from this
to determine reply-mode metadata (channel, sender, thread info).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingOutbound:
    """Buffered outbound message for deferred console display."""

    channel: str
    recipient: str | None
    body: str
    attachments: list[str] = field(default_factory=list)


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
