"""Message schema for agent queue protocol."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class InboundMessage:
    """A message entering the agent queue from any channel."""

    channel: str  # "cli", "line", "system"
    content: str
    priority: int  # 0 = highest
    sender: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OutboundMessage:
    """A response routed back to the originating channel."""

    channel: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingOutbound:
    """An outbound message awaiting retry (for unreliable channels like LINE)."""

    message: OutboundMessage
    retry_count: int = 0
    max_retries: int = 3
    next_retry: datetime | None = None


@dataclass
class ShutdownSentinel:
    """Transient control signal to stop the queue loop. Never persisted."""

    graceful: bool = True
