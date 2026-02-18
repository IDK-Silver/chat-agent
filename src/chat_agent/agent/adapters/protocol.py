"""Channel adapter protocol for message routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..schema import OutboundMessage

if TYPE_CHECKING:
    from ..core import AgentCore


class ChannelAdapter(Protocol):
    """Protocol that all channel adapters must satisfy.

    Adapters bridge external message sources (CLI, LINE, scheduler, etc.)
    with the AgentCore queue.
    """

    channel_name: str
    priority: int

    def start(self, agent: AgentCore) -> None:
        """Start the adapter. Called once before the queue loop begins."""
        ...

    def send(self, message: OutboundMessage) -> None:
        """Deliver a response to this channel."""
        ...

    def on_turn_complete(self) -> None:
        """Called after AgentCore finishes processing a turn from this channel.

        CLI adapter uses this to signal the input thread that the prompt
        can be shown again.  Other adapters may no-op.
        """
        ...

    def stop(self) -> None:
        """Stop the adapter. Called when the agent shuts down."""
        ...
